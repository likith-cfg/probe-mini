# Probe installed

Start with:

```
/probe-help
```

Generate and audit monitoring config for a service:

```
/probe generate monitoring config for <service-name>
```

Describe what you need in plain language. Probe asks for any missing details,
generates into a staging directory, audits the result, and shows you what it
plans to copy before changing your service repository. In a single-service
repository, Probe uses the current workspace directly. In a monorepo, it asks
you for the service's repository-relative path before inspecting service files.

You can also audit existing monitoring config:

```
/probe audit monitoring config in <directory>
```

Verify that deployed monitoring resources landed and work:

```
/probe-verify <GCP project ID> <service name>
```

Bundled Sabre standards refresh every 30 days. `/probe` reports whether they
are current before using them.
