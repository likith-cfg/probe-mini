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
| `/probe-verify` | `probe-verify` | Diagnose and fix a failed deployment using GCP Advisor evidence |
| `/probe-help` | `probe-help` | This message |

GCP Advisor needs no browser login or API token.

Docs are bundled in `docs/baseline/` and refresh from gitdocs.sabre.com
every 30 days (tracked in `docs/.last_refresh.json`). Sources are grouped
in `docs/registry.yaml` by `category`; `/probe` reads this small index and
opens only the minimum relevant baseline documents:
- `alerts` — Sabre's GKE alert-authoring guide, the alerting standard,
  and the Terraform alert-policy reference
- `dashboards` — Sabre's GKE dashboard-authoring guide
- `metrics` — SysEng's `GCP_ApplicationMetrics.html` metric-naming catalog,
  the metrics standard, and the Metrics NGP Starter how-to guides
- `logs` — Sabre's logging standard (structure, PII/PCI, cost)
