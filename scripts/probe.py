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
import subprocess
import sys
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
"""

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
                        pql = cond.get("conditionPrometheusQueryLanguage", {}).get("query", "")
                        query += pql
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


def _access_token() -> str:
    out = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _get_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def cmd_verify(args: argparse.Namespace) -> None:
    token = _access_token()
    name_filter = args.display_name_contains

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

    if not policies and not dashes:
        print(
            "\nNo resources found live in GCP yet. Checking Cloud Audit Logs for any "
            "attempted-and-failed Create* call would show the root cause if a deploy "
            "was attempted; absence of any Create* entry usually means the pipeline "
            "hasn't run (or hasn't reached GCP) rather than failed at the GCP API layer."
        )

    if args.chg:
        print(f"\n=== SRE Advisor analysis for {args.chg} ===")
        sre_url = f"https://sre-advisor-core-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/advice/change/{args.chg}"
        print(f"GET {sre_url}")
        try:
            req = urllib.request.Request(sre_url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                print(resp.read().decode())
        except Exception as e:  # noqa: BLE001 - surface any network/auth error to the agent
            print(f"SRE Advisor request failed: {e}")
            print("(SRE Advisor may require SSO/VPN access not available from this shell — "
                  "try the UI instead: https://sre-advisor-ui-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/)")


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
