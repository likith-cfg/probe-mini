---
name: probe
description: >
  Use only when explicitly invoked with /probe to generate or audit GCP
  observability config for a Sabre GKE service.
license: MIT
disable-model-invocation: true
---

# probe

Generate and audit Sabre GCP alert policies, dashboards, and service monitors
against the standards indexed by `docs/registry.yaml`.

## Plugin paths

Resolve `scripts/` and `docs/` from this skill's installed plugin root, never
from the open workspace. This file ends in `skills/probe/SKILL.md`; remove that
suffix to obtain `<root>`. Use absolute paths such as:

```bash
python3 <root>/scripts/probe.py check-refresh
```

Do not search the workspace for plugin files. Repository searches described
below apply only to the confirmed service root.

## 1. Refresh and select standards

Run once per session:

```bash
python3 <root>/scripts/probe.py check-refresh
```

- Exit `0`: docs are stale or absent. Fetch each URL in
  `<root>/docs/registry.yaml` into `<root>/docs/baseline/<slug>.md`. After all
  sources are checked successfully, run
  `python3 <root>/scripts/probe.py mark-refreshed`, even if their content is
  unchanged. Do not mark a partial or failed refresh.
- Exit `1`: docs are fresh. Tell the user their age in days. Do not run
  `mark-refreshed`.

Read `docs/registry.yaml` and choose the smallest sufficient source set. Start
with one source and add another only for a distinct concern not covered by the
first. Never load or scan every baseline document.

- Documentation-only request: answer from the selected source and stop.
- Audit-only request: continue at section 5; select more docs only if findings
  require them.
- Generation request: select only the sources needed for alerts, the dashboard,
  and PodMonitoring when using GMP, then continue.
- Deployment verification belongs to `/probe-verify`.

## 2. Confirm generation inputs

Repository evidence supports a suggestion but never replaces user
confirmation. Scope every lookup to `service_root`; never inspect sibling
services.

Collect and confirm:

- `service_root`: use `.` only when the workspace clearly contains one service.
  In a monorepo or ambiguous workspace, ask for the service-relative path before
  inspecting files.
- `app_name` and `project_name` (the deployment namespace).
- `monitoring_scope`: shared or one environment. Offer only exact environment
  names found in the service root's `BUILD` metadata and
  `configuration/vars/app/env/<environment>` directories. Preserve case and
  punctuation. If the sources disagree, show both sets and ask which existing
  directory convention to follow.
- HTTP server presence and downstream HTTP/gRPC/PubSub/MOM/Redis dependencies.
  Discover these as described in **Service signals** below, then confirm them.
- `metric_stack`: `gmp` or `stackdriver`. Discover it as described in
  **Metric stack** below, then confirm it.
- ServiceNow values: `u_service`, `u_assignment_group`, and a real
  `u_kb_article` (`KB########`). These are values from the service's existing
  ServiceNow records, not resources to create in the repository. Never invent a
  real-looking KB number. If no runbook exists, use `KB0000000`, clearly state
  that it must be replaced before production, and direct the user to create a
  runbook in ServiceNow Knowledge Base.
- `notification_channel`: resolve it as described in **Notification channel**
  below, then confirm it with the user.

### Notification channel

Channels are project-level, not service-specific. For the confirmed environment:

1. Read only `configuration/vars/app/env/<environment>/application*.{yaml,yml}`.
   If `snow_notification_channel` contains
   `projects/<project>/notificationChannels/<id>`, offer it first.
2. Otherwise read only the matching
   `configuration/files/helm/env/<environment>/` application config. Use a
  monitoring exporter `project-id`, or `spring.cloud.gcp.project-id` as a
  candidate. For the confirmed target project, run:

```bash
python3 <root>/scripts/probe.py notification-channels --project <project>
```

   The script obtains the active access token from `gcloud`; do not build a
   `curl` command. Offer only returned channels and let the user choose.
3. If no candidate or usable channel exists, ask for one. On auth or permission
   errors, ask the user to authenticate or obtain Monitoring access. Do not use
   `BUILD` workload project IDs unless monitoring config confirms them; workload
   and GKE Ops projects may differ. Never guess. Mark a borrowed channel as
   temporary because alerts route there until replaced.

### Service signals

Inspect service signals in this order and stop when evidence is conclusive:

1. `configuration/vars/app/service-specific.yaml` and existing monitoring or
   deployment manifests. Named ports/routes reveal HTTP or gRPC servers;
   monitor resources and environment references may reveal integrations.
2. `src/main/resources/application*.yml`, `application*.yaml`,
   `application*.properties`, and deployed app-config equivalents. Read only
   matching snippets for HTTP client URLs, gRPC, PubSub, MOM/JMS, or Redis.
3. The service/module `pom.xml`, `build.gradle`, or `build.gradle.kts`. Read only
   matching dependency lines for web servers/clients and the named integration
   technologies.

Treat an explicit server port/route as server evidence and an outbound endpoint
or client usage as downstream evidence. Generic libraries alone are not
conclusive because they may be transitive or used in the opposite direction.

If those files are inconclusive, search source files within `service_root` for
framework annotations and client construction/usages associated with the
unresolved technology. Do not read unrelated source files or expand into sibling
services. Report uncertainty when evidence is ambiguous; do not guess.

Show the evidence and ask the user to confirm the detected HTTP server and
dependency list.

### Metric stack

Inspect these locations in order and stop at the first conclusive signal:

1. Existing monitoring manifests: `PodMonitoring` or `ServiceMonitor` suggests
   `gmp`.
2. Application config and `service-specific.yaml`: `/actuator/prometheus` or
   Prometheus export suggests `gmp`; `management.stackdriver.metrics.export.*`
   suggests `stackdriver`.
3. The service/module `pom.xml`, `build.gradle`, or `build.gradle.kts`:
   `micrometer-registry-prometheus` suggests `gmp` and
   `micrometer-registry-stackdriver` suggests `stackdriver`.

Show the evidence and ask the user to confirm. Present conflicting signals
without recommending either stack. If no signal exists, ask the user to choose
without suggesting a default.

## 3. Generate in staging

Prefer customer-impact alerts such as workload failures
(`completion_code_category != "SUCCESS"`) and open dependency circuit breakers.
Do not add CPU or GC alerts unless the user ties them to an SLO.

```bash
python3 <root>/scripts/probe.py generate \
  --app-name <app> --project-name <namespace> \
  --u-service "<u_service>" --u-assignment-group "<group>" \
  --u-kb-article <KB or KB0000000> \
  --notification-channel "<channel resource name or empty>" \
  --metric-stack <gmp|stackdriver> \
  [--http] [--downstream-http <name1> <name2>] \
  --out <staging-dir>
```

Use only confirmed values. Pass `--http` only for a confirmed HTTP server and
omit `--downstream-http` when no downstream HTTP services were found.

## 4. Complete the dashboard

Generation leaves an empty `widgets` array. Use the selected dashboard standard
to add widgets for metrics the service actually emits, such as HTTP/gRPC
throughput and latency, workload failures, open circuit breakers, or JVM/GC.
Never deliver an empty dashboard skeleton.

## 5. Audit

```bash
python3 <root>/scripts/probe.py audit <staging-or-existing-directory>
```

Fix every `[ERROR]`. Explain every `[WARN]` to the user, including placeholders
and standards exceptions; do not treat warnings as lint noise.

## 6. Install after PASS

Copy generated files only after the audit passes. Follow the confirmed service's
existing monitoring layout and artifact directories such as `alertpolicies`,
`dashboards`, and `servicemonitors`.

- Shared scope: use the existing common monitoring location.
- Environment scope: use only the exact confirmed environment directory.
- Never duplicate shared config into every environment.
- Never `git`/`s2` commit or push without explicit user approval.

This skill does not generate logging config. Answer logging questions from the
selected standards source.
