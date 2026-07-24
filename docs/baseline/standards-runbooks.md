---
title: "Observability Team Documentation :: Runbooks"
source: "https://gitdocs.sabre.com/gitdocs/observability-docs/observability-standards/runbooks.html"
snapshot-date: "2026-07-24"
---

# Runbooks

A runbook is a step-by-step guide for troubleshooting and resolving an
incident. It must be easy to follow and self-contained enough to act on.

**A runbook is a type of Knowledge Base (KB) article and is mandatory for
every application.** It is created in ServiceNow (Knowledge Base -> Create
New), based on existing templates, in cooperation between the dev team and
the SRE team. Runbooks can be per-service or shared across a set of related
services.

Keep runbooks as simple as possible — link out to deeper gitdocs articles
rather than duplicating detail. The runbook itself only needs enough
information for the OCC team to correctly react to an alert.

**Implication for `u_kb_article` in alert documentation:** this field must
reference a *real* ServiceNow KB article number (format `KB` + 7 digits,
e.g. `KB0038125`). There is no automated way to generate one — it must
either already exist (many teams reuse one runbook article across several
alerts, e.g. one KB article shared by 3+ alerts for the same service) or be
created by someone with KB-author rights in the owning assignment group.
`probe-mini` will never fabricate a real-looking KB number; it uses an
obviously-fake placeholder (`KB0000000`) until a real one is supplied.
