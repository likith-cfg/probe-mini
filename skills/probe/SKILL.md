---
name: probe
description: >
  Generate and audit GCP observability config (alert policies, dashboards,
  service monitors/PodMonitoring) for a Sabre GKE service against Sabre's
  Observability/SysEng standards. Only invoked explicitly
  via the `/probe` command — do not auto-trigger this from ambient mentions
  of monitoring/alerts/dashboards in conversation.
license: MIT
disable-model-invocation: true
---

# probe

You generate and audit Sabre GCP monitoring config. Standards live in
`docs/baseline/*.md`.

Optimize context use: `docs/registry.yaml` is the small routing index;
`docs/baseline/*.md` contains the large source documents. Never scan or load
all baseline files.

## 0. Refresh docs if stale (only once per session, not per request)

```bash
python3 scripts/probe.py check-refresh
```

Exit code `0` = stale (or first run ever) → for each `url` in
`docs/registry.yaml`, call your web-fetch tool on that URL and overwrite
`docs/baseline/<slug>.md` with the result, then run
`python3 scripts/probe.py mark-refreshed`. 
Exit code `1` = fresh → continue to step 1 without fetching.
Never skip this check silently — either refresh or
explicitly tell the user docs are fresh (age in days) before proceeding.

## 1. Select the minimum relevant docs (every request)

After the refresh check, read `docs/registry.yaml` once. Match the request to
a `category`, then use the descriptions in that category to select the
smallest sufficient set of sources. Open only the selected
`docs/baseline/<slug>.md` files.

- Start with one source. Add another only when the first does not cover the
  task or the request clearly spans both sources.
- Do not open every source in a category and never scan all baseline files.
- Match concrete terms in the request (platform, framework, resource kind,
  task, error, and named technology) against the descriptions. Do not select
  a source merely because it shares the broad category.
- When a request spans distinct concerns (for example metric selection,
  exporter/query behavior, and deployment format), select one source for each
  concern whose description explicitly matches.
- Deployment verification belongs to `/probe-verify` and does not use this
  documentation index.

If the user asks only a documentation question, answer from the selected
docs and stop.

If the user asks only to audit existing files, skip steps 2-5 and run step 6
on the provided directory. Use its findings to select any additional source
through step 1; do not preload generation docs.

For generation, continue through steps 2-7. Because `generate` always writes
alerts and a dashboard (plus PodMonitoring for GMP), select the minimum source
for each artifact before editing it. Do not load unrelated implementation docs.

## 2. Gather the required facts before generating anything

**Always get the user's explicit confirmation for every field below. Never
silently use repository values.** Proactive lookups are limited to the
`service_root`, environment, and `metric_stack` checks below. Do not broadly
scan the repository. Evidence supports a suggestion; it never replaces
confirmation.

Ask the user for:

- `service_root`: the repository-relative directory containing the service.
  If the workspace root clearly contains one service, use `.` without asking.
  If it is a monorepo, the root is not itself a service, or multiple service
  locations are possible, ask the user for the service path before inspecting
  service files (for example `services/dcs-provider`). Scope every repository
  lookup below to this directory and never search sibling services.
- `monitoring_scope`: ask whether the generated monitoring config should be
  shared by all environments or created for one specific environment. Build
  the environment choices only from exact names found in the service's root
  `BUILD` deployment metadata and existing
  `configuration/vars/app/env/<environment>` directories. Preserve spelling,
  case, and punctuation exactly (for example `GCP-Dev`, never `dev` or
  `gcp-dev`). Do not invent or normalize names. If the user chooses a specific
  environment, ask them to select one of those exact names. If the two sources
  disagree, show both sets and ask which existing directory convention to
  follow before generating.
- Whether the service has an HTTP server, and any downstream HTTP/gRPC/
  PubSub/MOM/Redis dependencies
- `metric_stack`: inspect only these predefined locations, in order, and stop
  at the first conclusive signal:
  1. Existing monitoring manifests in the target repo's conventional
    monitoring directory. `PodMonitoring` or `ServiceMonitor` suggests
    `gmp`.
  2. `src/main/resources/application*.yml`, `application*.yaml`, and
    `application*.properties`, plus an existing `service-specific.yaml`.
    `/actuator/prometheus` or Prometheus export settings suggest `gmp`;
    `management.stackdriver.metrics.export.*` suggests `stackdriver`.
  3. Root `pom.xml`, `build.gradle`, or `build.gradle.kts`.
    `micrometer-registry-prometheus` suggests `gmp` and
    `micrometer-registry-stackdriver` suggests `stackdriver`.
  Read only the matching lines or smallest useful snippet. Do not search
  elsewhere unless the user asks. Show the evidence and source file, then ask
  the user to confirm the suggested stack or choose the other one. If signals
  conflict, present both without a recommendation. If no signal is found, ask
  the user to choose `gmp` or classic `stackdriver` without suggesting a
  default.
- ServiceNow fields: `u_service`, `u_assignment_group`, `u_kb_article`
  (a real `KB########` number). **Never invent a real-looking KB number.**
  If the user has none yet, use
  the obviously-fake placeholder `KB0000000` and tell them explicitly it
  must be replaced before this reaches prod — get a real runbook KB article
  from ServiceNow (Knowledge Base → Create New, with CKI/SRE)
- `notification_channel`: a real GCP notification channel resource name.
  If none is configured yet for this service, offer to list existing
  channels in the target project (`monitoring.googleapis.com/v3/projects/
  {project}/notificationChannels`) and ask the user to pick a temporary one
  to borrow, clearly flagged as temporary (real alerts will route there
  until swapped).

## 3. Apply customer-impact alerting

Alerts must target **customer-facing impact**, not internal resource metrics.
Do NOT default to CPU-usage or GC-pause alerts unless the user explicitly
ties them to an SLO — prefer
workload-failure alerts (`completion_code_category != "SUCCESS"`) and
circuit-breaker-open alerts for dependencies instead.

## 4. Generate

```bash
python3 scripts/probe.py generate \
  --app-name <app> --project-name <namespace> \
  --u-service "<u_service>" --u-assignment-group "<group>" \
  --u-kb-article <KB or KB0000000> \
  --notification-channel "<channel resource name or empty>" \
  --metric-stack <gmp|stackdriver> \
  [--http] [--downstream-http <name1> <name2>] \
  --out <staging dir, e.g. /tmp/<app>-monitoring>
```

Pass `--http` only when confirmed. Omit `--downstream-http` when there are no
HTTP dependencies. Use the confirmed metric stack; never copy the example
values blindly.

## 5. Fill in the generated dashboard

`generate` only writes a dashboard skeleton with an empty `widgets` array —
GMP doesn't support auto-populating one. Before moving on, use the dashboard
doc selected in step 1 and add real widgets for the metrics this service
actually emits (for example HTTP/gRPC throughput and latency, workload
failures, open circuit breakers, or JVM/GC). Don't leave the empty skeleton as
the final output.

## 6. Audit before writing into any real repo

```bash
python3 scripts/probe.py audit <staging dir>
```

Fix every `[ERROR]`. For `[WARN]` findings, explain them to the user (e.g.
`placeholder-kb-article`, `cpu-gc-alert-without-slo`) rather than silently
ignoring them — these map to real standards violations, not lint noise.

## 7. Only after PASS, copy into the real repo

Within the confirmed `service_root`, follow the target repo's existing
monitoring layout. For `monitoring_scope=shared`, use its common monitoring
location (for example `configuration/vars/app/common/monitoring/`). For an
environment-specific scope, use the exact selected environment directory (for
example `configuration/vars/app/env/GCP-Dev/monitoring/`). Create files only
for the confirmed scope; never duplicate shared config into every environment.
Preserve the artifact subdirectories used by the repo, such as
`alertpolicies`, `dashboards`, and `servicemonitors`. Never `git`/`s2` commit
or push without the user's explicit go-ahead — that's a shared-repo action,
not yours to take alone.

This skill doesn't generate logging config. Answer logging questions from
the source selected in step 1.
