---
name: probe
description: >
  Generate and audit GCP observability config (alert policies, dashboards,
  service monitors/PodMonitoring) for a Sabre GKE service against Sabre's
  Observability/SysEng standards, then commit it. Only invoked explicitly
  via the `/probe` command — do not auto-trigger this from ambient mentions
  of monitoring/alerts/dashboards in conversation.
license: MIT
disable-model-invocation: true
---

# probe

You generate and audit Sabre GCP monitoring config. Standards live in
`docs/baseline/*.md` (bundled) and refresh from gitdocs every 30 days.

## 0. Refresh docs if stale (do this once per session, not per request)

```bash
python3 scripts/probe.py check-refresh
```

Exit code `0` = stale (or first run ever) → for each `url` in
`docs/registry.yaml`, call your web-fetch tool on that URL and overwrite
`docs/baseline/<slug>.md` with the result, then run
`python3 scripts/probe.py mark-refreshed`. Exit code `1` = fresh → skip
straight to step 1. Never skip this check silently — either refresh or
explicitly tell the user docs are fresh (age in days) before proceeding.

## 1. Gather the required facts before generating anything

**Always ask the user directly for every field below. Never infer, guess,
or silently pull values from the target repo (`pom.xml`/`build.gradle`,
a `service-specific.yaml`, Terraform `tfvars`, etc.), even if signals for
them are clearly present in the codebase.** If you notice a signal in the
repo that suggests an answer, you may mention it to the user as a
suggestion, but you must still ask them to explicitly confirm or override
it before using it — never proceed on inference alone.

Ask the user for:

- `app_name` / `project_name` (k8s namespace — ask explicitly, don't assume)
- Whether the service has an HTTP server, and any downstream HTTP/gRPC/
  PubSub/MOM/Redis dependencies (drives circuit-breaker alerts, see
  `docs/baseline/gcp-application-metrics.md` for the `<type>-cb-<name>`
  naming convention)
- `metric_stack`: ask the user whether this service is on `gmp` or classic
  Stackdriver metrics — do not silently decide this by scanning the repo
  for `management.stackdriver.metrics.export.metric-type-prefix` or similar
  config. If you spot such config, point it out to the user as a hint, but
  still have them confirm which stack applies.
- ServiceNow fields: `u_service`, `u_assignment_group`, `u_kb_article`
  (a REAL `KB########` number — see `docs/baseline/standards-runbooks.md`.
  **Never invent a real-looking KB number.** If the user has none yet, use
  the obviously-fake placeholder `KB0000000` and tell them explicitly it
  must be replaced before this reaches prod — get a real runbook KB article
  from ServiceNow (Knowledge Base → Create New, with CKI/SRE) or an
  existing one the assignment group already reuses.)
- `notification_channel`: a real GCP notification channel resource name.
  If none is configured yet for this service, offer to list existing
  channels in the target project (`monitoring.googleapis.com/v3/projects/
  {project}/notificationChannels`) and ask the user to pick a temporary one
  to borrow, clearly flagged as temporary (real alerts will route there
  until swapped).

## 2. Apply the alerting standard, not just the field checklist

Per `docs/baseline/standards-alerts.md`: **alerts must target customer-facing
impact**, not internal resource metrics. Do NOT default to CPU-usage or GC-
pause alerts unless the user explicitly ties them to an SLO — prefer
workload-failure alerts (`completion_code_category != "SUCCESS"`) and
circuit-breaker-open alerts for dependencies instead.

## 3. Generate

```bash
python3 scripts/probe.py generate \
  --app-name <app> --project-name <namespace> \
  --u-service "<u_service>" --u-assignment-group "<group>" \
  --u-kb-article <KB or KB0000000> \
  --notification-channel "<channel resource name or empty>" \
  --metric-stack gmp --http \
  --downstream-http <name1> <name2> \
  --out <staging dir, e.g. /tmp/<app>-monitoring>
```

## 4. Audit before writing into any real repo

```bash
python3 scripts/probe.py audit <staging dir>
```

Fix every `[ERROR]`. For `[WARN]` findings, explain them to the user (e.g.
`placeholder-kb-article`, `cpu-gc-alert-without-slo`) rather than silently
ignoring them — these map to real standards violations, not lint noise.

## 5. Only after PASS, copy into the real repo

Follow the target repo's own conventions for where monitoring config lives
(e.g. `configuration/vars/app/common/monitoring/{alertpolicies,dashboards,
servicemonitors}/`). Never `git`/`s2` commit or push without the user's
explicit go-ahead — that's a shared-repo action, not yours to take alone.
