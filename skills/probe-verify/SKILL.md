---
name: probe-verify
description: >
  Check whether a GCP monitoring config change (or a deployment in general)
  actually succeeded or failed. Use whenever the user asks "did the
  deployment work", "is this alert live", "check if it failed", "why did it
  fail", or wants root-cause analysis of a change against real metrics. This
  is a DIFFERENT concern from the probe skill (which only generates/audits
  local config, with zero deployment).
license: MIT
---

# probe-verify

There is no single "one API" for this — use the layered approach below,
cheapest/fastest check first.

## Layer 1 — does the resource exist in GCP at all? (instant, no CHG needed)

```bash
python3 scripts/probe.py verify --project <project-id> --display-name-contains <app-name>
```

This calls the GCP Monitoring API directly (`alertPolicies`, `dashboards`)
with `gcloud auth print-access-token` — no Armada/CI access needed, just a
bearer token. It tells you existence + `mutatedBy`/who last touched it. It
does **not** tell you why something is missing.

Note: `ServiceMonitor`/`PodMonitoring` is a **Kubernetes CRD**, not a GCP
Monitoring API resource — it can't be checked this way. If asked to verify
one and `kubectl` + cluster credentials aren't available, say so explicitly
rather than guessing; the reliable proxy is Layer 1's metric check (below).

## Layer 2 — is it actually scraping/flowing? (real proof for ServiceMonitor)

Query `timeSeries` for the metric the ServiceMonitor should be feeding,
scoped to the service's namespace, and check for recent data points:

```
GET https://monitoring.googleapis.com/v3/projects/{project}/timeSeries
    ?filter=metric.type="prometheus.googleapis.com/http_server_requests_count/gauge"
     AND resource.labels.namespace_name="{namespace}"
```

Recent points = scraping is working end to end, regardless of whether you
can see the CRD itself.

## Layer 3 — root cause of a failure (Cloud Audit Logs, still no Armada needed)

If Layer 1 shows the resource missing, check whether a deploy was even
*attempted* before assuming failure:

```
POST https://logging.googleapis.com/v2/entries:list
{
  "resourceNames": ["projects/{project}"],
  "filter": "protoPayload.authenticationInfo.principalEmail=\"gke-iac-sa@{project}.iam.gserviceaccount.com\"
             AND protoPayload.serviceName=\"monitoring.googleapis.com\"
             AND protoPayload.methodName:\"Create\"",
  "orderBy": "timestamp desc"
}
```

(Same bearer token as Layer 1.) Interpretation:
- A `Create*` entry with a non-empty `status` (error code/message) → **that
  message is the root cause** (bad PromQL, IAM denial, quota, bad field).
  Explain it plainly and propose the fix (usually a config/audit issue —
  loop back to the `probe` skill to correct and regenerate).
- No `Create*` entry at all → the failure/delay is happening **before**
  GCP ever saw the request (bad YAML caught by a linter, failed test,
  pipeline queued/blocked, wrong target branch). This part genuinely
  requires looking at CI/Armada directly — say so, don't guess further.

## Layer 4 — real success/failure verdict with metric correlation: SRE Advisor

Sabre's actual tool for "did this change cause a problem" is **SRE
Advisor** (see `docs/baseline/sre-advisor-index.md`,
`docs/baseline/sre-advisor-config.md`). It correlates a SNOW Change's
commits against GCP Monitoring metrics before/after the deploy window and
returns an LLM-generated verdict (severity + recommendations).

```bash
python3 scripts/probe.py verify --project <project-id> \
  --display-name-contains <app-name> --chg CHG1234567
```

This calls `GET https://sre-advisor-core-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/advice/change/{chg}`.
Requirements before this works:
- The SNOW Change needs "commits list" enabled (Dora Lead Time).
- For best results, add a repo-local `sre-advisor.json` (schema in
  `docs/baseline/sre-advisor-config.md`) so it checks metrics specific to
  this service's own alert queries, not just generic CPU/memory.
- Best run ~2h after the Change completes; avoid analyzing Dev-env Changes
  or Changes within 24h of another Change to the same service.
- If the request fails (network/SSO), it likely needs VPN/SSO not
  available from this shell — point the user at the UI instead:
  https://sre-advisor-ui-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/

## When the user asks "did it succeed or fail"

1. Run Layer 1. If found → report success with `mutatedBy`/timestamp, done.
2. If missing, run Layer 3 to distinguish "not attempted yet" vs "attempted
   and rejected by GCP" — report which, and the exact error if there is one.
3. If they have (or can get) a CHG number and want causal metric analysis,
   run Layer 4 and relay the full report.
4. If you found a real error (Layer 3's status message, or an audit
   violation from the `probe` skill), **propose and, with confirmation,
   apply the fix** — don't just report the problem and stop.
