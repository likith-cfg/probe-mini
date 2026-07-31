---
name: probe-verify
description: >
  Analyze a failed GCP monitoring deployment from its ServiceNow change number.
  Only invoked explicitly via `/probe-verify`.
license: MIT
disable-model-invocation: true
---

# probe-verify

**Resolve every relative path below (`scripts/probe.py`) against this
plugin's own installed root, not the open workspace.** This file's own
absolute path always ends in `skills/probe-verify/SKILL.md`; strip that
suffix to get the root, then invoke the script using the resulting absolute
path (for example `python3 <root>/scripts/probe.py advisor --chg <CHG>`).
Never `file_search`/`grep_search` the workspace for `probe.py` — it belongs
to the plugin installation and will not be found there.

## GCP Advisor

Ask the user for the exact ServiceNow change number, such as `CHG1234567`, if
it is not already present in the request. Do not attempt to discover it from
repository files or other systems.

Run:

```bash
python3 scripts/probe.py advisor --chg <CHG>
```

GCP Advisor needs no browser login, API token, or gcloud authentication.
Report its `Error`, `Advice`, `Details`, and events without guessing beyond
the returned evidence.

If the advice identifies a file, locate it only within the service root given
by the user. Propose the smallest fix and apply it only after confirmation.
Run the narrowest relevant audit or test after editing.

If Advisor returns HTTP 404, ask the user to verify the change number. If it
returns no useful analysis, say so; do not fall back to deployment discovery.

## Optional direct GCP checks

When the user also wants current resource state or Cloud Audit Log evidence,
ask for the GCP project and monitoring display-name prefix, then run:

```bash
python3 scripts/probe.py verify \
  --project <project-id> \
  --display-name-contains <name>
```

This command checks matching alert policies and dashboards, then queries recent
failed Monitoring API create calls. It requires an authenticated `gcloud` CLI.

Exit codes:

- `0`: check completed.
- `2`: environment or authentication prevented the check; no verdict.
- `3`: API or usage error; no verdict.