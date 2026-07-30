"""Cloud Datastore client for Probe's shared known-fixes knowledge base."""
from __future__ import annotations

import datetime
import difflib
import hashlib
import json
import re
import urllib.error
import urllib.request

KB_PROJECT = "sab-dev-fsd-db-pad-5397"
KB_NAMESPACE = "probe-agent"
KB_KIND = "KnownFix"
DATASTORE_BASE = f"https://datastore.googleapis.com/v1/projects/{KB_PROJECT}"

SIMILARITY_THRESHOLD = 0.85
OUTCOME_WEIGHTS = {"yes": 1.0, "not_sure": 0.25, "no": -1.0}
MAX_EXAMPLES = 10

_NORMALIZE_PATTERNS = [
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<UUID>"),
    (re.compile(r"\S+@\S+\.\S+"), "<EMAIL>"),
    (re.compile(r"projects/[^/\s\"]+"), "projects/<PROJECT>"),
    (re.compile(r'"[^"]{2,80}"'), '"<NAME>"'),
    (re.compile(r"\b\d+\b"), "<NUM>"),
]


class KbError(Exception):
    """Raised for any Datastore REST call failure (network, auth, HTTP)."""


def normalize_error(message: str) -> str:
    """Replace variable error fields so equivalent failures cluster."""
    text = message.strip()
    for pattern, repl in _NORMALIZE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _pattern_id(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


def _entity_key(name: str) -> dict:
    return {
        "partitionId": {"projectId": KB_PROJECT, "namespaceId": KB_NAMESPACE},
        "path": [{"kind": KB_KIND, "name": name}],
    }


def _post(url: str, token: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace") if e.fp else ""
        raise KbError(f"HTTP {e.code} calling {url}: {body_text[:300]}") from e
    except urllib.error.URLError as e:
        raise KbError(f"Network error calling {url}: {e.reason}") from e


def _entity_to_dict(entity: dict) -> dict:
    out = {"name": entity["key"]["path"][-1]["name"]}
    for k, v in entity.get("properties", {}).items():
        if "stringValue" in v:
            out[k] = v["stringValue"]
        elif "integerValue" in v:
            out[k] = int(v["integerValue"])
        elif "doubleValue" in v:
            out[k] = float(v["doubleValue"])
        elif "timestampValue" in v:
            out[k] = v["timestampValue"]
        elif "arrayValue" in v:
            out[k] = [item.get("stringValue") for item in v["arrayValue"].get("values", [])]
        else:
            out[k] = v
    return out


def fetch_all(token: str) -> list[dict]:
    """Fetch all known fixes."""
    url = f"{DATASTORE_BASE}:runQuery"
    body = {
        "partitionId": {"projectId": KB_PROJECT, "namespaceId": KB_NAMESPACE},
        "query": {"kind": [{"name": KB_KIND}]},
    }
    results = []
    cursor = None
    while True:
        if cursor:
            body["query"]["startCursor"] = cursor
        data = _post(url, token, body)
        batch = data.get("batch", {})
        for r in batch.get("entityResults", []):
            results.append(_entity_to_dict(r["entity"]))
        if batch.get("moreResults") != "NOT_FINISHED":
            break
        cursor = batch.get("endCursor")
        if not cursor:
            break
    return results


def find_match(error_message: str, entities: list[dict]) -> dict | None:
    """Find an exact normalized match, then a fuzzy fallback."""
    normalized = normalize_error(error_message)
    for e in entities:
        if e.get("pattern_normalized") == normalized:
            return e
    best, best_score = None, 0.0
    for e in entities:
        score = difflib.SequenceMatcher(None, normalized, e.get("pattern_normalized", "")).ratio()
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= SIMILARITY_THRESHOLD else None


def confidence_label(entity: dict) -> str:
    votes = entity.get("vote_count", 0) or 0
    score_sum = entity.get("score_sum", 0.0) or 0.0
    if votes == 0:
        return "[unconfirmed]"
    avg = score_sum / votes
    if avg <= -0.3:
        return f"\u26a0 known NOT to reliably work ({votes} vote(s), avg {avg:.2f})"
    if avg < 0.4:
        return f"[unconfirmed] ({votes} vote(s), avg {avg:.2f})"
    return f"[confirmed] ({votes} vote(s), avg {avg:.2f})"


def _lookup_in_transaction(token: str, name: str, txn: str) -> dict | None:
    body = {
        "keys": [_entity_key(name)],
        "readOptions": {"transaction": txn},
    }
    data = _post(f"{DATASTORE_BASE}:lookup", token, body)
    found = data.get("found", [])
    if not found:
        return None
    return _entity_to_dict(found[0]["entity"])


def submit(
    token: str,
    error_message: str,
    fix: str,
    outcome: str,
    category: str = "",
    principal: str = "",
) -> dict:
    """Upsert a KnownFix entity: create it if this is a brand-new normalized
    pattern (or near-duplicate cluster), otherwise record another outcome
    vote against the existing one.

    Which entity to target is decided from a plain (possibly slightly stale)
    read; the actual counter increment is then computed from a fresh,
    transactional re-read of that specific entity immediately before commit,
    so concurrent submitters don't lose an update to score_sum/vote_count."""
    if outcome not in OUTCOME_WEIGHTS:
        raise ValueError(f"outcome must be one of {sorted(OUTCOME_WEIGHTS)}")

    normalized = normalize_error(error_message)
    match = find_match(error_message, fetch_all(token))
    name = match["name"] if match else _pattern_id(normalized)

    txn = _post(f"{DATASTORE_BASE}:beginTransaction", token, {})["transaction"]
    current = _lookup_in_transaction(token, name, txn)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    if current:
        score_sum = float(current.get("score_sum", 0.0)) + OUTCOME_WEIGHTS[outcome]
        vote_count = int(current.get("vote_count", 0)) + 1
        examples = current.get("pattern_examples", []) or []
        if error_message not in examples:
            examples = (examples + [error_message])[-MAX_EXAMPLES:]
        contributors = current.get("contributors", []) or []
        if principal and principal not in contributors:
            contributors = contributors + [principal]
        fix_text = current.get("fix") or fix
        created_at = current.get("created_at", now)
        category_val = current.get("category") or category
    else:
        score_sum = OUTCOME_WEIGHTS[outcome]
        vote_count = 1
        examples = [error_message]
        contributors = [principal] if principal else []
        fix_text = fix
        created_at = now
        category_val = category

    entity = {
        "key": _entity_key(name),
        "properties": {
            "pattern_normalized": {"stringValue": normalized, "excludeFromIndexes": True},
            "pattern_examples": {
                "arrayValue": {"values": [{"stringValue": e, "excludeFromIndexes": True} for e in examples]}
            },
            "fix": {"stringValue": fix_text, "excludeFromIndexes": True},
            "category": {"stringValue": category_val or ""},
            "score_sum": {"doubleValue": score_sum},
            "vote_count": {"integerValue": vote_count},
            "contributors": {"arrayValue": {"values": [{"stringValue": c} for c in contributors]}},
            "created_at": {"timestampValue": created_at},
            "last_seen_at": {"timestampValue": now},
        },
    }
    _post(f"{DATASTORE_BASE}:commit", token, {"transaction": txn, "mutations": [{"upsert": entity}]})
    return {
        "name": name,
        "normalized": normalized,
        "vote_count": vote_count,
        "score_sum": score_sum,
        "created": current is None,
    }


SEED_FIXES = [
    (
        "generic::invalid_argument: The following PromQL metric(s) are invalid: "
        "http_server_requests_count",
        "The alert-policy create-time metric validator can't statically verify a "
        "'_count'/'_sum' series derived from a Prometheus histogram (e.g. "
        "http_server_requests_count off http_server_requests/histogram) and rejects "
        "it with INVALID_ARGUMENT even though the query is valid at evaluation time. "
        "FIX: add 'disableMetricValidation: true' to that condition's "
        "conditionPrometheusQueryLanguage block (this is GCP's own sanctioned bypass "
        "for exactly this case), then re-apply.",
        "promql-metric-validation",
    ),
    (
        "PERMISSION_DENIED: The caller does not have permission",
        "The deploying service account lacks an IAM role it needs (commonly "
        "'Monitoring Editor' on the target project, or 'roles/monitoring.notificationChannelViewer' "
        "for the referenced notification channel). FIX: check IAM bindings for the "
        "principal shown, grant the missing role, then re-apply.",
        "iam-permission",
    ),
    (
        'notificationChannels/1234567890123456789 does not exist',
        "The alert policy references a notificationChannels resource name that "
        "doesn't exist in this project (wrong project ID, typo, or borrowed "
        "channel from a different project). FIX: list real channels with "
        "'v3/projects/{project}/notificationChannels' and use one that exists "
        "in this exact project.",
        "notification-channel",
    ),
]


def seed_from_static(token: str, principal: str = "seed-migration") -> list[dict]:
    """Add two confirmation votes for each bundled fix."""
    results = []
    for message, fix, category in SEED_FIXES:
        for _ in range(2):
            result = submit(token, message, fix, "yes", category=category, principal=principal)
        results.append(result)
    return results
