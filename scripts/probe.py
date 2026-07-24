#!/usr/bin/env python3
"""probe-mini: generate/audit GCP monitoring config, and verify a deployment.

Stdlib + PyYAML only (no Jinja2/pydantic) so this runs standalone on any
machine with Python 3.9+ and `pip install pyyaml` if not already present.
Subcommands:

  probe.py generate --app-name X --project-name X --u-service X \\
      --u-assignment-group X [--u-kb-article X] [--notification-channel X] \\
      [--metric-stack gmp|stackdriver] [--http] [--out DIR]

  probe.py audit DIR

  probe.py verify --project PROJECT [--display-name-contains NAME]
      [--chg CHG1234567]

  probe.py check-refresh [--registry docs/registry.yaml]
      [--state docs/.last_refresh.json]
"""
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
import urllib.request

try:
    import yaml
except ImportError:
    print("PyYAML is required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

CB_TYPES = {"http", "grpc", "pubsub", "mom", "redis", "datastore", "gcs"}
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
# disableMetricValidation is set by default: GCP's alert-policy create-time
# validator can't statically verify _count/_sum series derived from a
# Prometheus histogram (e.g. http_server_requests_count off
# http_server_requests/histogram) and rejects with INVALID_ARGUMENT even
# though the query is valid and works at evaluation time. This is the
# officially sanctioned GCP bypass for exactly that false positive.

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
            raw = open(path).read()
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
                    if str(parsed.get("u_kb_article", "")).strip() in ("", "CHANGE_ME"):
                        warnings.append(f"{path}: placeholder-kb-article — u_kb_article is empty/CHANGE_ME, needs a real ServiceNow KB number")
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
                            "observability-standards/alerts.md discourages this unless tied to an SLO"
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
    """Raised when verify can't even run its checks — missing/misconfigured
    gcloud, expired credentials, or an IAM permission gap. This is NEVER a
    verdict on the user's deployment; it must be reported as a distinct,
    clearly-flagged category so it isn't mistaken for '0 resources found'."""

    def __init__(self, message: str, remediation: str):
        super().__init__(message)
        self.message = message
        self.remediation = remediation


class ApiUsageIssue(Exception):
    """Raised for non-auth API errors (e.g. bad/nonexistent --project, malformed
    request) — not an auth/env gap, but still not a deployment verdict, so it
    must not be silently reported as '0 matching'."""


def _preflight_gcloud_auth() -> str:
    """Check gcloud is installed and authenticated, and return a bearer token.
    Raises EnvironmentIssue with a specific, actionable remediation instead of
    a bare traceback or a misleading '0 matching' result."""
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
    return token_result.stdout.strip()


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


# Known GCP error-message patterns -> (what it means, the concrete fix).
# Grown empirically from real Armada/Cloud-Audit-Log failures — add to this
# as new failure modes are diagnosed, don't just report-and-forget them.
KNOWN_FIXES = [
    (
        re.compile(r"PromQL metric\(s\) are invalid", re.I),
        "The alert-policy create-time metric validator can't statically verify a "
        "'_count'/'_sum' series derived from a Prometheus histogram (e.g. "
        "http_server_requests_count off http_server_requests/histogram) and rejects "
        "it with INVALID_ARGUMENT even though the query is valid at evaluation time. "
        "FIX: add 'disableMetricValidation: true' to that condition's "
        "conditionPrometheusQueryLanguage block (this is GCP's own sanctioned bypass "
        "for exactly this case), then re-apply.",
    ),
    (
        re.compile(r"permission.*denied|PERMISSION_DENIED", re.I),
        "The deploying service account lacks an IAM role it needs (commonly "
        "'Monitoring Editor' on the target project, or 'roles/monitoring.notificationChannelViewer' "
        "for the referenced notification channel). FIX: check IAM bindings for the "
        "principal shown, grant the missing role, then re-apply.",
    ),
    (
        re.compile(r"notificationChannel.*not found|does not exist", re.I),
        "The alert policy references a notificationChannels resource name that "
        "doesn't exist in this project (wrong project ID, typo, or borrowed "
        "channel from a different project). FIX: list real channels with "
        "'v3/projects/{project}/notificationChannels' and use one that exists "
        "in this exact project.",
    ),
]


def _diagnose_audit_log_failures(project: str, name_filter: str, token: str) -> list[dict]:
    """Query Cloud Audit Logs for failed Create* calls whose request payload
    mentions name_filter, and match each error message against KNOWN_FIXES.
    This is what lets the agent 'get this data and fix the issue' without the
    user needing to paste the Armada error message by hand."""
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
            fix = next((f for pat, f in KNOWN_FIXES if pat.search(message)), None)
            grouped[key] = {
                "first_seen": ts,
                "last_seen": ts,
                "attempts": 0,
                "principal": p.get("authenticationInfo", {}).get("principalEmail"),
                "display_name": display_name,
                "error_message": message,
                "suggested_fix": fix or "No known-fix pattern matched — needs manual investigation of this exact message.",
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
    try:
        token = _preflight_gcloud_auth()
    except EnvironmentIssue as e:
        _print_environment_issue(e)
        sys.exit(2)  # distinct exit code: inconclusive, not pass/fail

    name_filter = args.display_name_contains

    try:
        print(f"=== Alert policies matching {name_filter!r} in {args.project} ===")
        url = (
            f"https://monitoring.googleapis.com/v3/projects/{args.project}/alertPolicies"
            f"?filter=displayName:%22{name_filter}%22"
        )
        data = _get_json(url, token)
        policies = data.get("alertPolicies", [])
        print(f"{len(policies)} matching")
        for p in policies:
            print(" -", p.get("displayName"), "| mutatedBy:", p.get("mutatedBy"))

        print(f"\n=== Dashboards matching {name_filter!r} in {args.project} ===")
        url = f"https://monitoring.googleapis.com/v1/projects/{args.project}/dashboards"
        data = _get_json(url, token)
        dashes = [d for d in data.get("dashboards", []) if name_filter.lower() in (d.get("displayName") or "").lower()]
        print(f"{len(dashes)} matching")
        for d in dashes:
            print(" -", d.get("displayName"))

        print(f"\n=== Recent failed Create* attempts in Cloud Audit Logs (last 2 days) ===")
        findings = _diagnose_audit_log_failures(args.project, name_filter, token)
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

    if args.chg:
        print(f"\n=== SRE Advisor analysis for {args.chg} ===")
        sre_url = f"https://sre-advisor-core-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/advice/change/{args.chg}"
        print(f"GET {sre_url}")
        try:
            req = urllib.request.Request(sre_url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                print(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace") if e.fp else ""
            if e.code in (401, 403):
                _print_environment_issue(EnvironmentIssue(
                    f"SRE Advisor returned {e.code}: {body[:300]}",
                    "SRE Advisor needs Sabre SSO/VPN access from this shell — this is an "
                    "environment/auth gap, not a verdict on the change. Try the UI instead: "
                    "https://sre-advisor-ui-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/",
                ))
            else:
                print(f"SRE Advisor request failed: HTTP {e.code}: {body[:300]}")
        except urllib.error.URLError as e:
            _print_environment_issue(EnvironmentIssue(
                f"Network error calling SRE Advisor: {e.reason}",
                "SRE Advisor is only reachable from Sabre's internal network/VPN — this is an "
                "environment gap, not a verdict on the change. Try the UI instead: "
                "https://sre-advisor-ui-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/",
            ))


def cmd_check_refresh(args: argparse.Namespace) -> None:
    with open(args.state) as f:
        state = json.load(f)
    last = state.get("last_refresh")
    interval_days = yaml.safe_load(open(args.registry)).get("refresh_interval_days", 30)
    stale = True
    if last:
        last_dt = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
        age_days = (datetime.datetime.now(datetime.timezone.utc) - last_dt).days
        stale = age_days > interval_days
        print(f"last_refresh={last} age_days={age_days} interval_days={interval_days}")
    else:
        print("last_refresh=null (never refreshed)")
    print("STALE" if stale else "FRESH")
    sys.exit(0 if stale else 1)  # exit 0 (stale, action needed) / 1 (fresh, nothing to do)


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
    v.add_argument("--chg", default=None, help="SNOW Change ID for SRE Advisor analysis, e.g. CHG1234567")
    v.set_defaults(func=cmd_verify)

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
