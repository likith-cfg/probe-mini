# probe (probe-mini)

A lightweight Copilot CLI plugin for generating, auditing, and verifying Sabre
GCP observability configuration. Runtime code uses Python's standard library
plus PyYAML.

## Commands

- `/probe` generates and audits alert policies, dashboards, and PodMonitoring.
- `/probe-verify` asks for a ServiceNow change number and returns GCP Advisor's
  failure analysis. It can also run direct GCP resource and audit-log checks
  when given a project and display-name filter.
- `/probe-help` shows the command reference.

## Install

```bash
copilot plugin marketplace add likith-cfg/probe-mini
copilot plugin install probe@probe-mini
python3 -m pip install -r requirements.txt
```

## Use

```text
/probe generate monitoring config for <service-name>
/probe audit monitoring config in <directory>
/probe-verify
```

`/probe-verify` asks for the exact change number, such as `CHG1234567`. GCP
Advisor requires no browser session or gcloud token.

The `/probe` generation flow asks for the service root, monitoring scope,
metric stack, dependencies, ServiceNow fields, and notification channel. It
generates into a staging directory, audits the result, and asks before copying
files into the service repository.

Bundled standards are selected through `docs/registry.yaml`. The agent opens
only sources relevant to the request and refreshes stale snapshots from their
documented URLs.


## Layout

```text
.claude-plugin/       Copilot CLI plugin manifests
commands/             slash-command wrappers
docs/registry.yaml    standards source index and refresh interval
scripts/probe.py      generate, audit, Advisor, and direct GCP checks
scripts/kb_client.py  shared known-fixes client
skills/               agent workflows
tests/                unittest suite
requirements.txt      runtime Python dependency
```