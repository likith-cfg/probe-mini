# Probe installed

Start with `/probe-help`.

Install the Python dependency once:

```bash
python3 -m pip install -r requirements.txt
```

Generate or audit monitoring configuration:

```text
/probe generate monitoring config for <service-name>
/probe audit monitoring config in <directory>
```

Analyze a failed deployment:

```text
/probe-verify
```

Probe asks for the exact ServiceNow change number and calls GCP Advisor
directly. No browser setup is required.