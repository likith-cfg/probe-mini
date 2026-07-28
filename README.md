# probe (probe-mini)

A lightweight, standalone Copilot CLI plugin that generates and audits Sabre
GCP observability config (alert policies, dashboards, and PodMonitoring) and
verifies whether a real deployment succeeded — using Sabre's actual
standards docs and the real **SRE Advisor** service, not guesswork.

This is a self-contained skills-first sibling of the larger
[`probe`](https://github.com/sabre-internal/probe) Python package: no
`uv`/pydantic/Jinja2 dependency, just stdlib Python + PyYAML, so it works
anywhere Copilot CLI runs.

## What it does

- **`/probe`** — generate and audit alert policies, dashboards, and
  PodMonitoring for a service, enforcing Sabre standards (required ServiceNow
  doc fields, circuit-breaker naming, GMP vs MetricDescriptor rules,
  cardinality-risk checks, and the "no CPU/GC-only alerts unless SLO-tied"
  rule).
- **`/probe-verify`** — check whether a change actually landed and worked:
  layered checks from an instant GCP Monitoring API existence check, up
  through Cloud Audit Logs root-cause analysis, up to a full LLM-generated
  metric-correlation verdict from Sabre's SRE Advisor service.
- **`/probe-help`** — quick command reference.

Standards docs ship bundled in `docs/baseline/` (fetched from
gitdocs.sabre.com) and self-refresh every 30 days — the `probe` skill
checks `docs/.last_refresh.json` and re-fetches via the agent's own
web-fetch tool when stale (gitdocs needs an authenticated session, so
refreshing is agent-driven, not a bare script).

## Install

```bash
copilot plugin marketplace add sabre-internal/probe-mini
copilot plugin install probe@probe-mini
```

Or interactively inside Copilot CLI:

```
/plugin marketplace add sabre-internal/probe-mini
/plugin install probe@probe-mini
```

Commands are then available as `/probe`, `/probe-verify`, `/probe-help`
(or namespaced as `/probe-mini:probe` etc., depending on your Copilot CLI
version).

## Use

Start with the command reference:

```text
/probe-help
```

Generate and audit monitoring config:

```text
/probe generate monitoring config for <service-name>
```

In a repository containing one service, Probe uses the current workspace as
the service root. In a monorepo, Probe asks for the service's
repository-relative path before inspecting any files and never searches
sibling services.

Probe asks for missing service, dependency, ServiceNow, and notification
details. To identify the metrics pipeline efficiently, it checks a small set
of known service configuration locations, suggests GMP or Stackdriver when
the evidence is conclusive, and asks you to confirm the choice. Generated
files stay in a staging directory until they pass audit and you approve
copying them into the service repository.

Audit existing monitoring config without generating anything:

```text
/probe audit monitoring config in <directory>
```

Verify deployed resources and diagnose failures:

```text
/probe-verify <GCP project ID> <service name>
```

## Requirements

- Python 3.9+ with `pyyaml` (`pip install pyyaml` if missing).
- `gcloud` CLI, authenticated, for `/probe-verify`'s direct GCP API checks.
- Network/SSO access to gitdocs.sabre.com and SRE Advisor for doc refresh
  and Layer 4 verification (both are Sabre-internal).

## Local script usage (no agent needed)

```bash
python3 scripts/probe.py generate --app-name my-svc --project-name my-ns \
  --u-service "My Service" --u-assignment-group "CKI-OPS" \
  --u-kb-article KB0000000 --metric-stack gmp --http --out /tmp/my-svc-monitoring

python3 scripts/probe.py audit /tmp/my-svc-monitoring

python3 scripts/probe.py verify --project my-gcp-project --display-name-contains my-svc
```

For `generate`, `--project-name` currently means the Kubernetes namespace,
not the GCP project ID. The local script writes to `--out`; repository
placement and monorepo service selection are handled by the `/probe` agent.

## Layout

```
.claude-plugin/       marketplace.json + plugin.json (Copilot CLI plugin manifest)
plugin.yaml           provides_commands / provides_skills
skills/probe/         guided generation + audit skill
skills/probe-verify/  deployment verification skill
skills/probe-help/    help skill
commands/*.toml       slash-command wrappers
scripts/probe.py      stdlib + pyyaml implementation (generate/audit/verify/refresh-state)
docs/registry.yaml    request-time source routing index + refresh interval
docs/.last_refresh.json  refresh timestamp state
docs/baseline/*.md    bundled standards snapshots (offline bootstrap)
```
