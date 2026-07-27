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
- Deployment verification belongs to `/probe-verify`, whose fixed workflow
  names its required verification docs directly and does not need this index.

If the user asks only a documentation question, answer from the selected
docs and stop.

If the user asks only to audit existing files, skip steps 2-5 and run step 6
on the provided directory. Use its findings to select any additional source
through step 1; do not preload generation docs.

For generation, continue through steps 2-7. Because `generate` always writes
alerts and a dashboard (plus PodMonitoring for GMP), select the minimum source
for each artifact before editing it. Do not load unrelated implementation docs.

## 2. Gather the required facts before generating anything

**Always ask the user directly for every field below. Never infer, guess,
or silently pull values from the target repo (`pom.xml`/`build.gradle`,
a `service-specific.yaml`, Terraform `tfvars`, etc.), even if signals for
them are clearly present in the codebase.** If you notice a signal in the
repo that suggests an answer, you may mention it to the user as a
suggestion, but you must still ask them to explicitly confirm or override
it before using it — never proceed on inference alone.

Ask the user for:

- Whether the service has an HTTP server, and any downstream HTTP/gRPC/
  PubSub/MOM/Redis dependencies
- `metric_stack`: ask the user whether this service is on `gmp` or classic
  Stackdriver metrics — do not silently decide this by scanning the repo
  for `management.stackdriver.metrics.export.metric-type-prefix` or similar
  config. If you spot such config, point it out to the user as a hint, but
  still have them confirm which stack applies.
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

Follow the target repo's own conventions for where monitoring config lives
(e.g. `configuration/vars/app/common/monitoring/{alertpolicies,dashboards,
servicemonitors}/`). Never `git`/`s2` commit or push without the user's
explicit go-ahead — that's a shared-repo action, not yours to take alone.

This skill doesn't generate logging config. Answer logging questions from
the source selected in step 1.
