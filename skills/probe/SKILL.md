---
name: probe
description: >
  Use only when explicitly invoked with /probe to generate or audit GCP
  observability config for a Sabre GKE service.
license: MIT
disable-model-invocation: true
---

# probe

Configure or audit observability for a Sabre GKE service using the current
standards indexed by `docs/registry.yaml`.

## Plugin paths

Resolve `scripts/` and `docs/` from this skill's installed plugin root, never
from the open workspace. This file ends in `skills/probe/SKILL.md`; remove that
suffix to obtain `<root>`. Use absolute paths such as:

```bash
python3 <root>/scripts/probe.py check-refresh
```

Do not search the workspace for plugin files. Repository searches described
below apply only to the confirmed service root.

## 1. Refresh standards

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

Do not select documents yet. First identify the service and its current setup.

## 2. Identify the service

Assume the user is a developer who knows standard observability concepts but is
new to Sabre's tooling and conventions. Explain Sabre-specific acronyms,
ownership, repository paths, and required configuration when first relevant.
Do not show internal workflow names such as `service_root` unless useful.

Before asking anything else or inspecting repository files, ask:

> Which folder contains the service? Use directly `.` if this workspace contains one
> service. If it contains several services, give me the path to this service.

Read the single `BUILD` file at the service root first if it exists. Extract its
`configuration_names` and ask which of those exact configurations are in scope;
allow multiple selections. If no service-root `BUILD` exists, derive the choices
from `configuration/`. Preserve exact spelling, case, and punctuation such as
`GCP-Dev`.

## 3. Resolve ambiguity

After selecting configurations, inspect:

1. `pom.xml`, `build.gradle`, or `build.gradle.kts`.
2. The deployable module's `src/main/resources/application*.properties`,
   `application*.yml`, `application*.yaml`, and logging configuration.
3. For each selected configuration, deployment variables under
   `configuration/vars/app` and its runtime file under
   `configuration/files/helm/env/<environment>/app-config`.
4. Source inside `service_root` only for a specific unresolved signal.

Report missing runtime files before editing. List Helm-only environments absent
from `BUILD`, such as `GCP-CI`, and ask whether they are in scope. Treat
`configuration/vars` as deployment inputs, never runtime application config.

Determine whether the request is for:

- a new service: configure logs, metrics, and tracing
- existing alerts or dashboards: audit or update them only when explicitly
  requested.

For a new service, tell the user that SRE creates and standardizes alerts and
dashboards. Do not ask for ServiceNow or notification-channel values. Never
remove existing monitoring objects merely because SRE owns new-service setup.

Ask a question only when the answer changes the files or standards used and
cannot be established safely from the repository. Typical ambiguities are:

- conflicting build and deployment configuration;
- unclear new-service versus existing-monitoring scope;
- a dependency present without evidence that the service uses it; or
- a custom metric or trace whose intended business event is unknown.

Ask one concise question at a time. Include the evidence found and offer only
repository-backed choices. Do not ask the user to confirm facts already proved
by consistent configuration. Never guess when evidence conflicts.

## 4. Read the selected documents

Use `docs/registry.yaml` only as a routing index. Its descriptions are not
source material. Select the smallest sufficient source set, then open and read
`<root>/docs/baseline/<slug>.md` for every selected entry before making
source-dependent claims or edits. Never load every baseline document.

- Metrics: select the standard and only the starter guides needed for the
  detected technology and task.
- Logs: select the standard and only the installation or customization guides
  needed for the detected setup.
- Tracing: select the ADR, standard, and only the starter guides needed for the
  detected setup.
- Existing alerts or dashboards: select their matching standards.
- Documentation-only request: answer from the selected document and stop.

Use exact properties, versions, and requirements from the baselines, not from
memory. If a required baseline is missing or failed to refresh, stop and report
that gap instead of inventing guidance. Deployment verification belongs to
`/probe-verify`.

## 5. Compare and change

Compare the service with the selected documents:

- Metrics: build dependencies, application and deployment properties, exported
  endpoints, instrumentation, labels, and only the detected service paths.
- Logs: dependencies, configuration, profiles, output, fields, security
  controls, and relevant usage.
- Tracing: dependencies, properties, automatic instrumentation, context
  propagation, sampling, and confirmed service paths.

Report each gap with the baseline slug that establishes it. Use the repository's
existing versions and style unless a selected baseline requires a change. Make
the smallest complete change and run the narrowest relevant checks.

For each application property or logging change, determine where that setting
is effective at runtime. Updating local `src/main/resources` is not sufficient
when environment-specific Helm app config overrides or replaces it. Apply:

- shared settings to local defaults and every selected environment runtime
  application file;
- environment-specific values only to their matching runtime file; and
- deployment variables to `configuration/vars` only when a template consumes
  them.

Preserve environment-specific names, projects, buckets, endpoints, and secrets.
Before finishing, compare the changed observability settings across local config
and every selected runtime app config. Do not report completion while a selected
environment is missing a required setting; list any intentional difference and
why it remains.

For an explicit audit of existing alerts or dashboards, run:

```bash
python3 <root>/scripts/probe.py audit <existing-monitoring-directory>
```

Fix every `[ERROR]`. Explain each `[WARN]` with its practical impact and source
slug. Preserve the existing common or environment-specific layout. Ask for
ServiceNow or notification-channel values only when an existing alert change
requires them and repository evidence cannot supply them.

## 6. Report

Summarize what was already present, what changed, and what remains for the user
or SRE team. Use the words logs, metrics, traces, alerts, and dashboards rather
than unexplained product abbreviations. Mention file paths as evidence, but do
not overwhelm the user with every file inspected.
