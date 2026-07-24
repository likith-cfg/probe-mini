---
title: "Systems Engineering Team :: How-To: Ensure Comprehensive Application Metrics (condensed)"
source: "https://gitdocs.sabre.com/gitdocs/syseng_team_page/main/how_to_guides/GCP_ApplicationMetrics.html"
snapshot-date: "2026-07-24"
---

# GCP Application Metrics (condensed baseline)

> DRAFT upstream doc — content may change; this is a condensed snapshot for
> offline bootstrap only. Refresh via docs/registry.yaml for the full text.

## completion.code.category (Workload Metrics)

Required custom tag on all Workload Metrics (HTTP server, gRPC server,
PubSub subscriber, MOM API provider, scheduled tasks). Allowed values:

- `CRITICAL` — alert on 1+ occurrence (e.g. invalid DB credentials)
- `MAJOR` — alert on abnormal increase in frequency (e.g. query timeout)
- `MINOR` — alert on significant increase in frequency of an expected-but-
  uncommon condition
- `SUCCESS` — workload completed successfully per customer expectations

Never add unique identifiers (user IDs, account numbers, UUIDs) as metric
tags — causes cardinality explosion and GCP cost blowup.

## Circuit breaker naming (Dependency Metrics)

`<destination_type> + '-cb-' + <destination_name>`, where
`destination_type` in {`http`, `grpc`, `pubsub`, `mom`, `redis`,
`datastore`, `gcs`}. Examples: `http-cb-passenger-api`,
`grpc-cb-inventory`, `redis-cb-session`, `pubsub-cb-flight-events`,
`mom-cb-pnr`, `datastore-cb-<kind>`, `gcs-cb-invoice-pdf-archive`.
Every downstream call must be wrapped in a Resilience4j circuit breaker
named this way.

## GMP (Google-Managed Prometheus)

Metric names use `prometheus.googleapis.com/<snake_case_name>/<gauge|histogram>`.
Resource type is `prometheus_target`. **MetricDescriptor CRDs are NOT
supported under GMP** — never generate them for a GMP-stack service.
