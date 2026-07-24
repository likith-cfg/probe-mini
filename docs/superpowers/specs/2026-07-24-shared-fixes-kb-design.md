# Design: shared GCP-backed known-fixes knowledge base

Date: 2026-07-24

## Problem

`scripts/probe.py` hardcodes `KNOWN_FIXES` as a small list of regexes matched
against Cloud Audit Log error messages. It only reflects failures one user
has personally diagnosed, so other users/agents hitting the same GCP error
can't benefit from a fix already worked out elsewhere.

## Goals

- Replace the hardcoded list with a shared, universal store other probe
  users/agents read from and contribute to.
- Keep `probe.py`'s "stdlib + PyYAML only" design intact — no new SDK
  dependency.
- Support confidence weighting: a submitted fix can be voted `yes` / `no` /
  `not_sure`, and low/negative-confidence fixes are surfaced (never hidden)
  with an appropriate warning label.
- Cluster near-duplicate error messages (same failure, different resource
  name/ID) onto one entry instead of fragmenting the knowledge base.

## Non-goals

- Semantic/embedding-based matching (a vector DB). GCP's error strings here
  are consistent machine-generated text, not free-form prose — plain
  normalization + `difflib` fuzzy matching is sufficient and keeps the
  dependency footprint at zero.
- Automatic outcome detection (e.g. inferring "yes" from recurrence-free
  audit logs). v1 requires an explicit `kb-submit` call from the agent/user.
- Multiple candidate fixes per cluster / fix supersession — v1 keeps one
  `fix` text per pattern cluster.

## Storage: Cloud Datastore (Datastore mode)

- Project: `sab-dev-fsd-db-pad-5397`
- Namespace: `probe-agent`
- Kind: `KnownFix`
- Accessed via the plain Datastore REST API
  (`https://datastore.googleapis.com/v1/projects/{project}:lookup|:runQuery|:commit|:beginTransaction`)
  using the same OAuth bearer token `_preflight_gcloud_auth()` already
  obtains from the user's own `gcloud` session — no new auth flow, no new
  dependency.

## Normalization & clustering

`normalize_error(message)` replaces variable substrings (UUIDs, long numeric
IDs, quoted names, `projects/<x>`, emails) with placeholders, producing a
canonical signature. The entity key name is `sha256(normalized)[:32]`, so the
common case (identical normalized signature) is a direct key lookup with no
query.

For genuinely new signatures, a fallback pass compares the new normalized
string against every existing entity's normalized string with
`difflib.SequenceMatcher` (kind is expected to stay in the hundreds, not
millions, so a full scan per submission is cheap); similarity >= 0.85 merges
into that entity's cluster instead of creating a duplicate.

## Entity fields

`pattern_normalized`, `pattern_examples` (capped list of raw messages),
`fix`, `category`, `score_sum`, `vote_count`, `contributors` (emails),
`created_at`, `last_seen_at`.

Confidence = `score_sum / vote_count`. Outcome weights: `yes = +1`,
`not_sure = +0.25`, `no = -1` (matches user-approved scheme).

## Write path: `probe.py kb-submit`

```
probe.py kb-submit --error-message TEXT --fix TEXT --outcome yes|no|not_sure [--category X]
```

Used both to contribute a brand-new pattern+fix and to vote on an existing
one (the agent runs this right after applying a fix and confirming, via
re-verify or its own testing, whether it worked).

To avoid lost updates on the vote counters under concurrent submitters, the
target entity (once identified by exact-hash or fuzzy-match lookup) is
re-read transactionally (`:beginTransaction` + `:lookup` with
`readOptions.transaction`) immediately before computing the new
`score_sum`/`vote_count`, and the increment is committed with that same
transaction ID. The initial fuzzy-match scan (deciding *which* cluster to
target) is a plain read and can be slightly stale — worst case is a rare
duplicate cluster, not a lost vote; acceptable for v1's expected write
volume.

## Read path: `probe.py verify`

`_diagnose_audit_log_failures` fetches all `KnownFix` entities once per run
(soft-fails with a warning if the KB is unreachable — this is an
enhancement, not core to `verify`), and for each newly-seen audit-log error
message looks up a match the same way (`normalize_error` + exact hash, then
`difflib` fallback). Confidence bands control the label shown:

- No match -> today's "no known-fix pattern matched" message, plus a hint to
  contribute via `kb-submit`.
- `vote_count == 0` -> `[unconfirmed]`.
- average score in `(-0.3, 0.4)` -> `[unconfirmed] (N votes, avg X)`.
- average score `<= -0.3` -> `⚠ known NOT to reliably work (N votes, avg X)`.
- average score `>= 0.4` -> `[confirmed] (N votes, avg X)`.

## Migration

Today's 3 hardcoded `KNOWN_FIXES` become `SEED_FIXES` (representative example
message + fix + category tuples) and a new `probe.py kb-seed` subcommand
upserts them as pre-confirmed entries (two synthetic `yes` votes each) — a
one-time, idempotent migration so behavior doesn't regress before the shared
KB has organic data.

## Code organization

New file `scripts/kb_client.py` holds all Datastore REST calls,
normalization, clustering, and scoring logic. `probe.py` imports it; the
Datastore-specific concern stays isolated and independently testable.
