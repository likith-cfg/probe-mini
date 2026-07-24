---
title: "Observability Team Documentation :: Alerts"
source: "https://gitdocs.sabre.com/gitdocs/observability-docs/observability-standards/alerts.html"
snapshot-date: "2026-07-24"
---

# Alerts

Alerts notify teams about critical issues that require immediate attention.
GCP Monitoring and SHP should be used as the standard for alerting in Sabre.

## ServiceNow and alerting

All alerts must be recorded in SNOW — it is the single source of truth for
OCC/SRE across all Sabre systems. All Sabre alerting tools integrate with
ServiceNow and create events/alerts/incidents there. See KB0033547 for GCP
Monitoring <-> SNOW event management integration details.

## SRE best practices for alerting

- Alerts should focus on actionable issues that require immediate attention.
- Avoid alert fatigue by minimizing noisy or low-priority alerts.
- Use well-defined thresholds/conditions to trigger alerts.
- Group related alerts to reduce redundancy.
- Route alerts to the appropriate teams/individuals.
- Regularly review and refine alerting rules.
- Prioritize alerts based on impact to Service Level Objectives (SLOs).
- **Alerts should focus on customer-facing impact, not internal system
  metrics. Using CPU usage or GC pause time as an alerting metric is
  generally discouraged** — even a large CPU/GC spike should not alert
  unless it directly impacts an SLO.

## Alerts as part of deployment

Deploying alerts/dashboards together with the application (IaC) is
recommended so every environment is monitored consistently. Some teams
deploy them together with app components; others use a separate process —
both are valid.

## Alerts costs since 2026

Since April 2026, GCP alerting policies carry additional cost. See
observability-docs/gcp-cost/gcp_alert_costs.html and GCP's own alerting
pricing docs before adding many fine-grained alert conditions.
