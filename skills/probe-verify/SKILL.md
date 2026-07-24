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

## Layer 3 — root cause of a failure, auto-diagnosed (Cloud Audit Logs, no Armada needed)

`scripts/probe.py verify` already does this automatically — you don't need
to paste in an Armada error message by hand. It queries Cloud Audit Logs for
failed `CreateAlertPolicy`/`CreateDashboard` calls matching the app name,
dedupes retries, and matches each error message against a `KNOWN_FIXES`
table (in `scripts/probe.py`) that maps a real GCP error pattern to a
concrete fix:

```bash
python3 scripts/probe.py verify --project <project-id> --display-name-contains <app-name>
```

This is what lets the agent close the loop end-to-end: change fails in
Armada → user (or you) runs `/probe-verify` → the exact error + a matching
fix comes back automatically, without needing Armada/CI access at all —
Cloud Audit Logs already recorded the same error Armada showed the user,
because both are just reporting the same underlying `gcloud`/Terraform
provider API call's response.

Known fixes currently in `KNOWN_FIXES` (extend this list whenever you
diagnose a new failure mode — don't just fix-and-forget):
- `"PromQL metric(s) are invalid"` → the query uses a `_count`/`_sum`
  suffix derived from a Prometheus histogram (e.g. `http_server_requests_count`
  off `http_server_requests/histogram`); GCP's alert-policy create-time
  validator can't statically verify these and rejects with
  `INVALID_ARGUMENT` even though the query is valid at evaluation time.
  Fix: add `disableMetricValidation: true` to that condition. (Real example:
  dcs-provider's "HTTP Workload Failures" alert, 2026-07-24 — 13 retries,
  all identical error, fixed by adding this field. `probe`'s generator and
  `audit` now default/check for this.)
- `PERMISSION_DENIED` → deploying service account missing an IAM role.
- notification channel not found → wrong/nonexistent channel resource name.

If `scripts/probe.py verify` finds a failure with **no** matching
`KNOWN_FIXES` entry, treat the raw `error_message` field as the ground
truth (don't guess) — investigate it, and once you've found the real fix,
**add a new entry to `KNOWN_FIXES`** so probe auto-diagnoses it next time
instead of requiring a human to re-discover it.

If Layer 1 shows the resource missing and Layer 3 finds **no** failed
`Create*` attempts either, the failure/delay is happening **before** GCP
ever saw the request (bad YAML caught by a linter, failed test, pipeline
queued/blocked, wrong target branch) — that genuinely requires looking at
CI/Armada directly; say so, don't guess further.

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

## Exit codes: distinguish "no verdict" from "pass/fail"

`scripts/probe.py verify` uses distinct exit codes so you never mistake a
broken check for a real result:

- `0` — the check ran to completion. This is a real verdict (whatever it
  printed — found/not-found, diagnosed failure, etc.) — safe to report as-is.
- `2` — **environment/auth problem** (`gcloud` missing, not logged in,
  expired/invalid token, missing IAM role, no network/VPN). The check
  itself could not run. **Never report this as "0 found" or "verification
  passed/failed"** — tell the user verification was inconclusive and relay
  the printed remediation (e.g. re-run `gcloud auth login`, request the
  missing IAM role, connect to VPN).
- `3` — **API/usage error** (e.g. a bad `--project` value returning HTTP
  404) — not an auth problem, not a deployment verdict either. Tell the
  user to double-check the argument they gave you (usually `--project` or
  `--display-name-contains`) rather than treating it as a failed deployment.

If you ever see exit code `2` or `3`, do not proceed to interpret "0
matching" as a real answer — surface the printed banner's Problem/
Remediation instead and ask the user to resolve it before re-running.
