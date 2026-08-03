#!/usr/bin/env python3
"""Generate, audit, and verify Sabre GCP monitoring configuration."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

import kb_client

REQUIRED_ALERT_DOC_FIELDS = {"severity", "u_service", "u_assignment_group", "u_kb_article"}
CARDINALITY_RISK_WORDS = {"user_id", "userid", "account_number", "uuid", "unique_id", "session_id"}

ALERT_POLICY_TEMPLATE = """---
apiVersion: monitoring.cnrm.cloud.google.com/v1beta1
kind: MonitoringAlertPolicy
metadata:
  labels:
    app: {app_name}
  name: {name}
  namespace: {namespace}
spec:
  notificationChannels:
    - name: {notification_channel}
  displayName: "{app_name} | {display_name}"
  documentation:
    mimeType: text/markdown
    content: |
      {{
        "description": "{app_name} | {description}",
        "severity": "{severity}",
        "u_service": "{u_service}",
        "u_assignment_group": "{u_assignment_group}",
        "u_kb_article": "{u_kb_article}"
      }}
  enabled: true
  combiner: OR
  conditions:
    - displayName: "{app_name} | {display_name}"
      conditionPrometheusQueryLanguage:
        query: |-
          {query}
        duration: 0s
        evaluationInterval: 60s
        disableMetricValidation: true
"""
# Required for synthetic _count/_sum series derived from Prometheus histograms.

DASHBOARD_TEMPLATE = """apiVersion: monitoring.cnrm.cloud.google.com/v1beta1
kind: MonitoringDashboard
metadata:
  labels:
    app: {app_name}
  name: {app_name}-dashboard
  namespace: {namespace}
spec:
  displayName: "{app_name} | Overview"
  dashboardJson: |
    {{
      "displayName": "{app_name} | Overview",
      "gridLayout": {{ "columns": "2", "widgets": [] }}
    }}
"""

SERVICEMONITOR_TEMPLATE = """apiVersion: monitoring.googleapis.com/v1
kind: PodMonitoring
metadata:
  name: {app_name}
  namespace: {namespace}
  labels:
    app: {app_name}
spec:
  selector:
    matchLabels:
      app: {app_name}
  endpoints:
    - port: actuator
      interval: 30s
      path: /actuator/prometheus
"""


def cmd_generate(args: argparse.Namespace) -> None:
    out_dir = args.out
    os.makedirs(os.path.join(out_dir, "alertpolicies"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "dashboards"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "servicemonitors"), exist_ok=True)

    namespace = args.project_name
    notification_channel = args.notification_channel or ""

    policies = []
    if args.http:
        policies.append(dict(
            name=f"{args.app_name}001",
            display_name="HTTP Workload Failures",
            description="HTTP CRITICAL/MAJOR completion codes > 0",
            severity="3",
            query=(
                'sum(rate(http_server_requests_count{completion_code_category!="SUCCESS", '
                'completion_code_category=~"CRITICAL|MAJOR"}[5m])) > 0'
            ),
        ))
    for i, downstream in enumerate(args.downstream_http, start=2):
        policies.append(dict(
            name=f"{args.app_name}{i:03d}",
            display_name=f"Circuit Breaker Open: http-cb-{downstream}",
            description=f"Circuit breaker http-cb-{downstream} is OPEN",
            severity="2",
            query=(
                f'resilience4j_circuitbreaker_state{{name="http-cb-{downstream}", state="open"}} == 1'
            ),
        ))

    alert_yaml_docs = []
    for p in policies:
        alert_yaml_docs.append(ALERT_POLICY_TEMPLATE.format(
            app_name=args.app_name,
            namespace=namespace,
            notification_channel=notification_channel,
            u_service=args.u_service,
            u_assignment_group=args.u_assignment_group,
            u_kb_article=args.u_kb_article,
            **p,
        ))
    alert_path = os.path.join(out_dir, "alertpolicies", f"{args.app_name}-alerts.yaml.j2")
    with open(alert_path, "w") as f:
        f.write("\n".join(alert_yaml_docs))
    print(f"wrote {alert_path}")

    dash_path = os.path.join(out_dir, "dashboards", f"{args.app_name}-dashboard.yaml.j2")
    with open(dash_path, "w") as f:
        f.write(DASHBOARD_TEMPLATE.format(app_name=args.app_name, namespace=namespace))
    print(f"wrote {dash_path}")

    if args.metric_stack == "gmp":
        sm_path = os.path.join(out_dir, "servicemonitors", f"{args.app_name}-service-monitor.yaml.j2")
        with open(sm_path, "w") as f:
            f.write(SERVICEMONITOR_TEMPLATE.format(app_name=args.app_name, namespace=namespace))
        print(f"wrote {sm_path}")
        print("skipped metricdescriptors/ (GMP does not support MetricDescriptors)")


def _strip_jinja(text: str) -> str:
    text = re.sub(r"\{\{.*?\}\}", "PLACEHOLDER", text)
    text = re.sub(r"<<.*?>>", "PLACEHOLDER", text)
    return text


def cmd_audit(args: argparse.Namespace) -> None:
    directory = args.directory
    errors: list[str] = []
    warnings: list[str] = []
    files_scanned = 0

    for root, _dirs, files in os.walk(directory):
        for fname in files:
            if not (fname.endswith(".yaml") or fname.endswith(".yaml.j2")):
                continue
            path = os.path.join(root, fname)
            files_scanned += 1
            with open(path) as f:
                raw = f.read()
            try:
                docs = list(yaml.safe_load_all(_strip_jinja(raw)))
            except yaml.YAMLError as e:
                errors.append(f"{path}: yaml-parse-error — {e}")
                continue

            for doc in docs:
                if not doc:
                    continue
                kind = doc.get("kind", "")

                if kind == "MetricDescriptor":
                    warnings.append(f"{path}: gmp-no-metric-descriptor — MetricDescriptor found; not supported under GMP")

                if kind == "MonitoringAlertPolicy":
                    spec = doc.get("spec", {})
                    doc_content = spec.get("documentation", {}).get("content", "")
                    try:
                        parsed = json.loads(doc_content)
                    except (json.JSONDecodeError, TypeError):
                        errors.append(f"{path}: missing-alert-doc-field — documentation.content is not valid JSON")
                        continue
                    missing = REQUIRED_ALERT_DOC_FIELDS - set(parsed.keys())
                    if missing:
                        errors.append(f"{path}: missing-alert-doc-field — missing {sorted(missing)}")
                    if str(parsed.get("u_kb_article", "")).strip() in ("", "CHANGE_ME", "KB0000000"):
                        warnings.append(
                            f"{path}: placeholder-kb-article — u_kb_article is empty or a placeholder; "
                            "needs a real ServiceNow KB number"
                        )
                    severity = str(parsed.get("severity", ""))
                    if severity not in {"1", "2", "3", "4", "5"}:
                        errors.append(f"{path}: invalid-severity — severity {severity!r} not in 1..5")

                    query = ""
                    conditions = spec.get("conditions", [])
                    for cond in conditions:
                        pql_cond = cond.get("conditionPrometheusQueryLanguage")
                        if pql_cond is None:
                            continue
                        pql = pql_cond.get("query", "")
                        query += pql
                        if re.search(r"\b\w+_(count|sum)\b", pql) and not pql_cond.get("disableMetricValidation"):
                            warnings.append(
                                f"{path}: missing-disable-metric-validation — query uses a "
                                "'_count'/'_sum' suffix (likely derived from a Prometheus histogram); "
                                "GCP's alert-policy create-time validator rejects these with "
                                "INVALID_ARGUMENT unless disableMetricValidation: true is set. "
                                "This is the exact failure mode seen in dcs-provider's "
                                "'HTTP Workload Failures' alert on 2026-07-24."
                            )
                    lowered = query.lower()
                    for risky in CARDINALITY_RISK_WORDS:
                        if risky in lowered:
                            warnings.append(f"{path}: cardinality-risk — query references '{risky}'")
                    if ("cpu" in lowered or "gc_pause" in lowered) and "slo" not in raw.lower():
                        warnings.append(
                            f"{path}: cpu-gc-alert-without-slo — CPU/GC-based alert with no SLO link mentioned; "
                            "tie this alert to a customer-impact SLO before deploying it"
                        )

    print(f"AUDIT: {directory}")
    for e in errors:
        print(f"[ERROR] {e}")
    for w in warnings:
        print(f"[WARN]  {w}")
    passed = len(errors) == 0
    print(f"\n{len(errors)} errors, {len(warnings)} warnings in {files_scanned} files — {'PASS' if passed else 'FAIL'}")
    sys.exit(0 if passed else 1)


class EnvironmentIssue(Exception):
    """Raised when environment or authentication prevents verification."""

    def __init__(self, message: str, remediation: str):
        super().__init__(message)
        self.message = message
        self.remediation = remediation


class ApiUsageIssue(Exception):
    """Raised for non-auth API errors or malformed arguments."""


GCP_ADVISOR_BASE_URL = "https://dev-platform-advisor-ngp-mon-ci.apps.dev-03.us-central2.dev.sabre-gcp.com"


def _preflight_gcloud_auth() -> tuple[str, str]:
    """Return the active gcloud bearer token and account."""
    if shutil.which("gcloud") is None:
        raise EnvironmentIssue(
            "gcloud CLI is not installed / not on PATH.",
            "Install the Google Cloud SDK and ensure `gcloud` is on PATH, or run this "
            "from a shell where it's already set up.",
        )

    active = subprocess.run(
        ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        capture_output=True, text=True,
    )
    if active.returncode != 0:
        raise EnvironmentIssue(
            f"`gcloud auth list` failed: {active.stderr.strip()}",
            "Run `gcloud auth login` (or `gcloud auth application-default login`) to authenticate, "
            "then retry.",
        )
    if not active.stdout.strip():
        raise EnvironmentIssue(
            "No active gcloud account. `gcloud auth list` returned no ACTIVE account.",
            "Run `gcloud auth login` to authenticate, then retry.",
        )

    token_result = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True)
    if token_result.returncode != 0:
        raise EnvironmentIssue(
            f"`gcloud auth print-access-token` failed: {token_result.stderr.strip()}",
            "Your gcloud session may be expired or misconfigured. Run `gcloud auth login` again.",
        )
    return token_result.stdout.strip(), active.stdout.strip()


def _get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise _classify_http_error(e, url) from e
    except urllib.error.URLError as e:
        raise EnvironmentIssue(
            f"Network error calling {url}: {e.reason}",
            "Check network/VPN connectivity to *.googleapis.com from this shell.",
        ) from e


def _post_json(url: str, token: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise _classify_http_error(e, url) from e
    except urllib.error.URLError as e:
        raise EnvironmentIssue(
            f"Network error calling {url}: {e.reason}",
            "Check network/VPN connectivity to *.googleapis.com from this shell.",
        ) from e


def _list_notification_channels(project: str, token: str) -> list[dict]:
    url = f"https://monitoring.googleapis.com/v3/projects/{project}/notificationChannels?pageSize=100"
    channels: list[dict] = []
    while url:
        data = _get_json(url, token)
        channels.extend(data.get("notificationChannels", []))
        page_token = data.get("nextPageToken")
        url = f"{url.split('&pageToken=', 1)[0]}&{urllib.parse.urlencode({'pageToken': page_token})}" if page_token else ""
    return channels


def cmd_notification_channels(args: argparse.Namespace) -> None:
    try:
        token, _account = _preflight_gcloud_auth()
        channels = _list_notification_channels(args.project, token)
    except EnvironmentIssue as e:
        _print_environment_issue(e)
        sys.exit(2)
    except ApiUsageIssue as e:
        _print_api_usage_issue(e)
        sys.exit(3)

    print(f"{len(channels)} notification channel(s) in {args.project}")
    for channel in channels:
        print(
            f"{channel.get('name', '')}\t"
            f"type={channel.get('type', '')}\t"
            f"enabled={channel.get('enabled', False)}\t"
            f"displayName={channel.get('displayName', '')}"
        )


def _classify_http_error(e: "urllib.error.HTTPError", url: str) -> Exception:
    body = e.read().decode(errors="replace") if e.fp else ""
    if e.code == 401:
        return EnvironmentIssue(
            f"401 Unauthorized calling {url}: {body[:300]}",
            "Your gcloud access token is invalid or expired. Run `gcloud auth login` again.",
        )
    if e.code == 403:
        return EnvironmentIssue(
            f"403 Permission denied calling {url}: {body[:300]}",
            "The authenticated gcloud account lacks IAM permission on this project "
            "(needs at least 'Monitoring Viewer' and 'Logging Viewer'). Ask a project "
            "admin to grant it, or switch accounts with `gcloud auth login`.",
        )
    return ApiUsageIssue(f"HTTP {e.code} calling {url}: {body[:300]}")


def _get_advisor_json(chg: str) -> dict:
    chg_q = urllib.parse.quote(chg, safe="")
    url = f"{GCP_ADVISOR_BASE_URL}/advisor/{chg_q}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        payload = e.read().decode(errors="replace") if e.fp else ""
        if e.code == 404:
            raise ApiUsageIssue(
                f"GCP Advisor found no analysis for {chg} (HTTP 404). "
                "Double-check the change number."
            ) from e
        raise ApiUsageIssue(f"GCP Advisor returned HTTP {e.code} for {chg}: {payload[:300]}") from e
    except urllib.error.URLError as e:
        raise EnvironmentIssue(
            f"Network error calling GCP Advisor for {chg}: {e.reason}",
            "Check network/VPN connectivity to *.sabre-gcp.com from this shell.",
        ) from e
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise ApiUsageIssue(f"GCP Advisor returned invalid JSON for {chg}") from error


def cmd_advisor(args: argparse.Namespace) -> None:
    try:
        payload = _get_advisor_json(args.chg)
    except EnvironmentIssue as e:
        _print_environment_issue(e)
        sys.exit(2)
    except ApiUsageIssue as e:
        _print_api_usage_issue(e)
        sys.exit(3)

    print(f"=== GCP Advisor failure analysis for {payload.get('change', args.chg)} ===")
    print(f"Error:  {payload.get('error') or '(none reported)'}")
    print(f"Advice: {payload.get('advice') or '(none reported)'}")
    details = payload.get("details")
    if details:
        print(f"Details: {details}")
    events = payload.get("events") or []
    print(f"Events: {len(events) if isinstance(events, list) else 'n/a'}")
    for event in events if isinstance(events, list) else []:
        print(f" - {event}")


def _diagnose_audit_log_failures(project: str, name_filter: str, token: str) -> list[dict]:
    """Find failed Monitoring creates and match them to known fixes."""
    filter_str = (
        '(protoPayload.methodName="google.monitoring.v3.AlertPolicyService.CreateAlertPolicy" '
        'OR protoPayload.methodName="google.monitoring.v3.DashboardsService.CreateDashboard") '
        f'AND timestamp>="{(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    )
    body = {
        "resourceNames": [f"projects/{project}"],
        "filter": filter_str,
        "orderBy": "timestamp desc",
        "pageSize": 50,
    }
    data = _post_json("https://logging.googleapis.com/v2/entries:list", token, body)

    kb_entities: list[dict] = []
    kb_warning = None
    try:
        kb_entities = kb_client.fetch_all(token)
    except kb_client.KbError as e:
        # KB enrichment is optional; raw audit evidence remains useful.
        kb_warning = str(e)

    grouped: dict[tuple[str, str], dict] = {}
    for entry in data.get("entries", []):
        p = entry.get("protoPayload", {})
        status = p.get("status") or {}
        if not status.get("message"):
            continue  # empty status == success
        req = p.get("request", {})
        display_name = (
            req.get("alertPolicy", {}).get("displayName")
            or req.get("dashboard", {}).get("displayName")
            or ""
        )
        if name_filter.lower() not in display_name.lower():
            continue
        message = status["message"]
        key = (display_name, message)
        ts = entry.get("timestamp")
        if key not in grouped:
            match = kb_client.find_match(message, kb_entities) if kb_entities else None
            if match:
                suggested_fix = f"{match['fix']} {kb_client.confidence_label(match)}"
            elif kb_warning:
                suggested_fix = (
                    f"Could not check the shared known-fixes KB ({kb_warning}) — "
                    "needs manual investigation of this exact message."
                )
            else:
                suggested_fix = (
                    "No known-fix pattern matched in the shared KB — needs manual investigation "
                    "of this exact message. Once diagnosed, contribute it with "
                    "`probe.py kb-submit --error-message ... --fix ... --outcome yes` "
                    "so other users hitting the same error benefit."
                )
            grouped[key] = {
                "first_seen": ts,
                "last_seen": ts,
                "attempts": 0,
                "principal": p.get("authenticationInfo", {}).get("principalEmail"),
                "display_name": display_name,
                "error_message": message,
                "suggested_fix": suggested_fix,
            }
        grouped[key]["attempts"] += 1
        grouped[key]["first_seen"] = min(grouped[key]["first_seen"], ts)
        grouped[key]["last_seen"] = max(grouped[key]["last_seen"], ts)
    return list(grouped.values())


def _print_environment_issue(e: EnvironmentIssue) -> None:
    print("\n" + "=" * 70)
    print("⚠️  VERIFICATION COULD NOT RUN — environment/auth problem")
    print("This is NOT a verdict on the deployment/config — the check itself")
    print("could not execute. Do not report '0 found' as a result.")
    print("=" * 70)
    print(f"Problem:     {e.message}")
    print(f"Remediation: {e.remediation}")


def _print_api_usage_issue(e: ApiUsageIssue) -> None:
    print("\n" + "=" * 70)
    print("⚠️  VERIFICATION COULD NOT RUN — API/usage error (not auth, not a verdict)")
    print("Likely a bad argument (e.g. wrong --project) rather than a deployment result.")
    print("=" * 70)
    print(f"Problem: {e}")


def cmd_verify(args: argparse.Namespace) -> None:
    project = args.project
    name_filter = args.display_name_contains

    try:
        token, _account = _preflight_gcloud_auth()
    except EnvironmentIssue as e:
        _print_environment_issue(e)
        sys.exit(2)  # inconclusive, not pass/fail

    try:
        print(f"=== Alert policies matching {name_filter!r} in {project} ===")
        url = (
            f"https://monitoring.googleapis.com/v3/projects/{project}/alertPolicies"
            f"?filter=displayName:%22{name_filter}%22"
        )
        data = _get_json(url, token)
        policies = data.get("alertPolicies", [])
        print(f"{len(policies)} matching")
        for p in policies:
            print(" -", p.get("displayName"), "| mutatedBy:", p.get("mutatedBy"))

        print(f"\n=== Dashboards matching {name_filter!r} in {project} ===")
        url = f"https://monitoring.googleapis.com/v1/projects/{project}/dashboards"
        data = _get_json(url, token)
        dashes = [d for d in data.get("dashboards", []) if name_filter.lower() in (d.get("displayName") or "").lower()]
        print(f"{len(dashes)} matching")
        for d in dashes:
            print(" -", d.get("displayName"))

        print("\n=== Recent failed Create* attempts in Cloud Audit Logs (last 2 days) ===")
        findings = _diagnose_audit_log_failures(project, name_filter, token)
        if not findings:
            if not policies and not dashes:
                print(
                    "No failed Create* attempts found either. Likely the pipeline hasn't "
                    "run yet (or hasn't reached GCP) rather than failed at the GCP API layer."
                )
            else:
                print("No failed Create* attempts found.")
        for f in findings:
            print(f" - \"{f['display_name']}\" | {f['attempts']}x attempt(s), {f['first_seen']} -> {f['last_seen']} | {f['principal']}")
            print(f"   error: {f['error_message']}")
            print(f"   fix:   {f['suggested_fix']}")
    except EnvironmentIssue as e:
        _print_environment_issue(e)
        sys.exit(2)
    except ApiUsageIssue as e:
        _print_api_usage_issue(e)
        sys.exit(3)  # distinct exit code: bad usage, not pass/fail


def cmd_kb_submit(args: argparse.Namespace) -> None:
    try:
        token, account = _preflight_gcloud_auth()
    except EnvironmentIssue as e:
        _print_environment_issue(e)
        sys.exit(2)

    try:
        result = kb_client.submit(
            token,
            args.error_message,
            args.fix,
            args.outcome,
            category=args.category,
            principal=account,
        )
    except kb_client.KbError as e:
        print(f"\n⚠️  Could not write to the shared knowledge base: {e}", file=sys.stderr)
        sys.exit(2)

    action = "Created new entry" if result["created"] else "Updated existing entry"
    print(f"{action} {result['name']} (normalized: {result['normalized']!r})")
    print(f"vote_count={result['vote_count']} score_sum={result['score_sum']}")


def cmd_kb_seed(args: argparse.Namespace) -> None:
    try:
        token, account = _preflight_gcloud_auth()
    except EnvironmentIssue as e:
        _print_environment_issue(e)
        sys.exit(2)

    try:
        results = kb_client.seed_from_static(token, principal=account)
    except kb_client.KbError as e:
        print(f"\n⚠️  Could not seed the shared knowledge base: {e}", file=sys.stderr)
        sys.exit(2)

    for r in results:
        print(f"seeded {r['name']} (vote_count={r['vote_count']}, score_sum={r['score_sum']})")


def cmd_check_refresh(args: argparse.Namespace) -> None:
    state = {}
    if os.path.exists(args.state):
        with open(args.state) as f:
            state = json.load(f)
    last = state.get("last_refresh")
    with open(args.registry) as f:
        interval_days = yaml.safe_load(f).get("refresh_interval_days", 30)
    stale = True
    if last:
        last_dt = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
        age_days = (datetime.datetime.now(datetime.timezone.utc) - last_dt).days
        stale = age_days > interval_days
        print(f"last_refresh={last} age_days={age_days} interval_days={interval_days}")
    else:
        print("last_refresh=null (never refreshed)")
    print("STALE" if stale else "FRESH")
    sys.exit(0 if stale else 1)


def cmd_mark_refreshed(args: argparse.Namespace) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(args.state, "w") as f:
        json.dump({"last_refresh": now}, f, indent=2)
    print(f"wrote last_refresh={now} to {args.state}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate")
    g.add_argument("--app-name", required=True)
    g.add_argument("--project-name", required=True)
    g.add_argument("--u-service", required=True)
    g.add_argument("--u-assignment-group", required=True)
    g.add_argument("--u-kb-article", default="KB0000000")
    g.add_argument("--notification-channel", default="")
    g.add_argument("--metric-stack", choices=["gmp", "stackdriver"], default="gmp")
    g.add_argument("--http", action="store_true")
    g.add_argument("--downstream-http", nargs="*", default=[])
    g.add_argument("--out", required=True)
    g.set_defaults(func=cmd_generate)

    a = sub.add_parser("audit")
    a.add_argument("directory")
    a.set_defaults(func=cmd_audit)

    v = sub.add_parser("verify")
    v.add_argument("--project", required=True)
    v.add_argument("--display-name-contains", required=True)
    v.set_defaults(func=cmd_verify)

    nc = sub.add_parser("notification-channels", help="List notification channels in a GCP project")
    nc.add_argument("--project", required=True)
    nc.set_defaults(func=cmd_notification_channels)

    adv = sub.add_parser(
        "advisor",
        help="Get GCP Advisor's failure analysis for a ServiceNow change number (CHG)",
    )
    adv.add_argument("--chg", required=True, help="ServiceNow change number, e.g. CHG1234567")
    adv.set_defaults(func=cmd_advisor)

    ks = sub.add_parser("kb-submit", help="Contribute a fix to the shared known-fixes knowledge base")
    ks.add_argument("--error-message", required=True, help="The raw GCP error message you diagnosed")
    ks.add_argument("--fix", required=True, help="The fix text (only used if this is a new entry)")
    ks.add_argument("--outcome", required=True, choices=sorted(kb_client.OUTCOME_WEIGHTS),
                     help="Whether the fix worked: yes / no / not_sure")
    ks.add_argument("--category", default="", help="Optional short tag, e.g. iam-permission")
    ks.set_defaults(func=cmd_kb_submit)

    kseed = sub.add_parser("kb-seed", help="One-time idempotent migration of legacy hardcoded fixes into the shared KB")
    kseed.set_defaults(func=cmd_kb_seed)

    cr = sub.add_parser("check-refresh")
    cr.add_argument("--registry", default=os.path.join(os.path.dirname(__file__), "..", "docs", "registry.yaml"))
    cr.add_argument("--state", default=os.path.join(os.path.dirname(__file__), "..", "docs", ".last_refresh.json"))
    cr.set_defaults(func=cmd_check_refresh)

    mr = sub.add_parser("mark-refreshed")
    mr.add_argument("--state", default=os.path.join(os.path.dirname(__file__), "..", "docs", ".last_refresh.json"))
    mr.set_defaults(func=cmd_mark_refreshed)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
