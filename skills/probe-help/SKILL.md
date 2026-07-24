---
name: probe-help
description: >
  Show what the probe plugin can do. Only invoked explicitly via the
  `/probe-help` command.
license: MIT
disable-model-invocation: true
---

# probe-help

`probe` (this repo is `probe-mini`, plugin name `probe`) generates and
verifies Sabre GCP observability config.

| Command | Skill | What it does |
|---|---|---|
| `/probe` | `probe` | Generate + audit alert policies/dashboards/service monitors from Sabre standards |
| `/probe-verify` | `probe-verify` | Check if a config change / deployment actually landed and worked, layered: GCP API existence check → Cloud Audit Logs root-cause → SRE Advisor metric-correlation verdict |
| `/probe-help` | `probe-help` | This message |

Docs are bundled in `docs/baseline/` and refresh from gitdocs.sabre.com
every 30 days (tracked in `docs/.last_refresh.json`). Standards sources:
Observability team's `observability-standards/*` (alerts, runbooks,
dashboards, metrics, SLOs) and SysEng's `GCP_ApplicationMetrics.html`
(metric naming, circuit breakers, GMP).
