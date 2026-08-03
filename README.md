# probe

A Copilot CLI plugin for generating, auditing, and verifying Sabre GCP
observability configuration.

## Commands

| Command | Purpose |
|---|---|
| `/probe` | Generate or audit alert policies, dashboards, and service monitors |
| `/probe-verify` | Analyze a ServiceNow change with GCP Advisor; optionally check live GCP state |
| `/probe-help` | Show the command reference |

## Install

```bash
copilot plugin marketplace add likith-cfg/probe-mini
copilot plugin install probe@probe-mini
python3 -m pip install -r requirements.txt
```

Python 3 and PyYAML are required. Live GCP checks use the active `gcloud`
account; GCP Advisor does not require `gcloud` authentication.

## Use

```text
/probe generate monitoring config for <service-name>
/probe audit monitoring config in <directory>
/probe-verify CHG1234567
```

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts
```