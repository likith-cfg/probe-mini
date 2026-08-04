---
name: probe-verify
description: >
  Diagnose and fix a failed GCP deployment using GCP Advisor evidence from its
  ServiceNow change number. Only invoked explicitly via `/probe-verify`.
license: MIT
disable-model-invocation: true
---

# probe-verify

## Plugin path

Strip `skills/probe-verify/SKILL.md` from this file's installed absolute path to
obtain `<root>`. Run `<root>/scripts/probe.py` by absolute path. Never search the
open workspace for plugin files.

## 1. Collect evidence

Ask the user for the exact ServiceNow change number, such as `CHG1234567`, when
the request does not contain one. Do not discover or infer it from other files
or systems.

Run:

```bash
python3 <root>/scripts/probe.py advisor --chg <CHG>
```

Treat `Error`, `Advice`, `Details`, and events as the primary evidence. Do not
assume the advice is sufficient by itself or extend conclusions beyond the
returned data.

- Exit `0`: analysis completed.
- Exit `2`: network or environment prevented analysis; no verdict.
- Exit `3` with HTTP 404: ask the user to verify the CHG number.
- Other exit `3`: report the API or usage error; no verdict.

If Advisor returns no useful evidence, say so and stop. Do not search the
repository or change files speculatively.

## 2. Locate the root cause

Ask one concise question at a time only when its answer changes the next action
and cannot be established from the request or Advisor output. Include the
conflicting evidence and offer only evidence-backed choices. Never guess project
IDs, display names, paths, or CHG numbers.

Ask for the service root only when locating a reported file. If multiple files
or services match, ask which one is intended. Search only within the confirmed
root. Trace the exact Advisor error through the referenced file, nearby
configuration, and deployment inputs until the repository evidence supports a
root cause. Do not investigate unrelated warnings or guessed causes.

## 3. Fix and validate

Apply the smallest change that fixes the evidenced root cause. Preserve the
service's existing configuration style and do not change unrelated files. Run
the narrowest relevant audit, test, parse, or build check. If validation fails,
repair the same issue and rerun it; do not claim the deployment is fixed from a
local check alone.

Report the Advisor evidence, root cause, changed files, and validation result.
Clearly separate verified facts from anything still requiring redeployment.

## 4. Optional live GCP check

Only when the user requests current state or Cloud Audit Log evidence, ask for
any missing GCP project or monitoring display-name prefix, then run:

```bash
python3 <root>/scripts/probe.py verify \
  --project <project-id> \
  --display-name-contains <name>
```

This checks matching alert policies, dashboards, and recent failed Monitoring
API create calls. It requires authenticated `gcloud` access.

- Exit `0`: check completed; report only the returned evidence.
- Exit `2`: environment or authentication prevented the check; no verdict.
- Exit `3`: API or usage error; no verdict.

Never report an inconclusive check as zero matching resources or as a deployment
result.