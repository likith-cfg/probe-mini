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

Diagnose and fix a failed deployment:

```text
/probe-verify
```

Probe asks for the exact ServiceNow change number, uses GCP Advisor evidence to
find the root cause, and applies the smallest supported repository fix.