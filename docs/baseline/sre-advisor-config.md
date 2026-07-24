---
title: "SRE Advisor :: Configuration"
source: "https://gitdocs.sabre.com/gitdocs/sre-advisor/config.html"
snapshot-date: "2026-07-24"
---

# SRE Advisor configuration (condensed)

By default SRE Advisor runs generic CPU/memory queries against the GKE
cluster's ops project. To check metrics relevant to a *specific* service
(e.g. our `http_server_requests_count`/`completion_code_category` alert
query), add an `sre-advisor.json` file to the repo root (Sabre2: directory
containing the `BUILD` file) on the default branch.

## Shape

```json
{
  "defaultGKE": {
    "project": "<gkeops-project-with-metrics>",
    "queries": [
      {
        "description": "LLM-readable description of this timeseries",
        "prometheus": {
          "query": "<PromQL, recommend >=5m aggregation>",
          "step": "5m"
        },
        "delay": "15m"
      }
    ]
  },
  "<env-or-project-name>": {
    "project": "<override-project>",
    "queries": [ ]
  }
}
```

- Top-level keys: `defaultGKE`, `defaultTerraform`, or a specific `<env>`/
  `<project>` name (env has highest priority, then project, then default).
- Each query is either MQL (`query`) or PromQL (`prometheus.query` +
  `prometheus.step`) — mutually exclusive.
- `delay` postpones metric collection after the Change (useful for
  metrics that are naturally unstable right after a restart, e.g. JVM CPU).
- Variables available: `${namespace}`, `${cluster}` (GKE) or `${project}`,
  `${region}` (Terraform), plus `${artifactName}`.
