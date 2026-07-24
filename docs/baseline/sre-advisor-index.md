---
title: "SRE Advisor :: Overview"
source: "https://gitdocs.sabre.com/gitdocs/sre-advisor/index.html"
snapshot-date: "2026-07-24"
---

# SRE Advisor

SRE Advisor identifies correlations between metric anomalies and code
changes for a specific SNOW Change (CHG). This is the tool `probe-mini`
uses to answer "did our deployment succeed or fail" beyond a simple
resource-exists check.

## Workflow

1. Retrieve Change info from SNOW (start/end time, commits, automation data).
2. Collect commit patches from GitHub for those commits.
3. Retrieve `sre-advisor.json` config from the target repo (if present).
4. Query metrics from GCP Monitoring for 48h before / 2h after the Change.
5. Detect anomalies (Exponential Smoothing model, 3-sigma horizon).
6. Send code diff + anomaly data to Gemini 2.5-flash with a structured
   prompt.
7. Return a Markdown report: Warnings, Severity (INFO/MINOR/MAJOR/CRITICAL),
   Code Changes Summary, Metrics Deviations, Recommendations, Commits.

## Requirements

- Enable "commits list" in the SNOW Change (see Dora Lead Time docs).
- Optional: `sre-advisor.json` in the repo root (Sabre2: directory with the
  `BUILD` file) on the default branch, for custom PromQL/MQL queries.
- To query metrics from a non-default project, grant "Monitoring Viewer"
  to `saq73marwf7mltg4lytqyh453cs-1@sab-dev-gkeops-02607229.iam.gserviceaccount.com`
  in that project.

## Using it

- UI: https://sre-advisor-ui-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/
- API — Analyze Change:
  `GET https://sre-advisor-core-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/advice/change/{chg}`
  Optional query params: `metrics=false`, `code=false`, `refresh=true`,
  `customPrompt=...`
- API — Analyze Commits (no SNOW Change needed):
  `POST https://sre-advisor-core-sre-advisor.apps.dev-01.us-central1.dev.sabre-gcp.com/advice/commits`

## Limitations

- Best in stable environments; Dev-env analysis is less reliable.
- No other Changes should land 24h before the CHG under analysis.
- Wait ~2h after the CHG completes before requesting analysis.
- Multiple apps deployed to the same namespace can cause metric interference.
