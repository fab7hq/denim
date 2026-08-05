# Denim architecture

Denim is a small standard-library extension around two host skills and one
native executable. It has no workflow engine, database, background service,
provider framework, transcript recorder, role system, or compatibility reader.

The user owns the unit of work and its completion boundary. Multiple pending
asks may belong to one unit; `seal` is the user's explicit decision that the
unit is done. Denim stores the ask evidence and sealed batch, not a separate
unit or workflow object.

## Components

- `skills/ask/` supplies live host capability descriptors, performs one
  host-native invocation, and captures the exact final result.
- `skills/seal/` requests one explicit user-owned completion checkpoint.
- `discovery.py` contains the separate Claude and Codex normalization adapters
  and one deterministic resolver.
- `config.py` parses the closed TOML precedence chain.
- `ask.py` renders bounded prompts and commits closed outcomes.
- `store.py` validates and atomically commits immutable ask and batch artifacts.
- `seal.py` derives pending asks, holds the workspace lock, builds batches, and
  verifies exact bindings.
- `fab7.py` owns the only Fab7 subprocess and validates its bounded JSON and
  append-only ledger records.
- `cli.py` exposes public `ask` and `seal`; `verify-batch` is an internal Fab7
  verifier entrypoint and is omitted from public help.

The Fab7 extension builder renders the same skill source as Claude plugin
skills and Codex plugin skills. Host selection occurs at explicit build and
installation time, never by runtime guessing.

## Ask flow

1. The explicit host skill reads only capabilities advertised in the current
   turn and writes a temporary prepare request outside project source.
2. `denim ask` validates the complete descriptor snapshot, overlays permitted
   configuration on IDs that actually exist, and returns one selected
   capability plus a digest-bound ticket and enriched prompt.
3. The host invokes that capability at most once through its native surface.
4. The skill submits the ticket and exact result to `denim ask`.
5. Denim atomically commits the result and canonical record. A second commit
   for the same ask ID is rejected.

If resolution is unavailable, step 2 commits that truthful closed outcome and
no capability is invoked. A crash before final capture produces no ask record;
there is no mutable in-progress workflow state.

Resolution filters visibility, callability, result capture, and authority
before exact pins, configured routes, or ranking. The default origin order is
custom, built-in, plugin, fallback. Specific positive matches are ranked before
all `*` candidates; within an origin tier, match strength, priority, and lexical
ID are deterministic tie-breakers. Explicit configuration may replace the
origin order but cannot bypass eligibility or create a candidate.

A custom workflow crosses this boundary as one host-advertised capability and
one final result. Its native planning, tool calls, agents, and intermediate
state stay inside the host. A user-owned unit of work may contain multiple
such asks, but Denim still has no workflow type or workflow executor.

## State

State is workspace-scoped under the existing Fab7 runtime boundary:

```text
<workspace>/.fab7/denim/
├── .gitignore
├── asks/
│   ├── <ask-id>.json
│   └── <ask-id>.result
├── batches/
│   └── <batch-id>.json
└── seal.lock
```

The nested `.gitignore` contains `*`, so runtime data does not enter project
source. Files use owner-only modes and canonical UTF-8 JSON with one trailing
line feed. Digests bind those exact artifact bytes or exact result bytes.
Writes stage beneath the ignored runtime boundary, `fsync`, and link to a new
name without replacement. Existing old state is ignored; it is never imported,
upgraded, or treated as current state.

Ask records include the exact prompt, normalized selected descriptor, enriched
prompt, status, exact result digest, limitations, and effects. The exact result
is the adjacent `.result` file. A batch contains only ordered ask IDs, record
digests, result digests, and truthful statuses. Sealed state is derived from a
valid matching Fab7 claim and passing evidence; there is no mutable sealed flag.

## Seal concurrency and failure

One non-blocking operating-system lock excludes concurrent seal attempts.
While holding it, Denim validates every ask and batch, excludes asks referenced
by valid prior seals, and snapshots the remaining set. An ask committed after
that snapshot is not added retroactively.

The Fab7 command uses ordered argv, `shell=False`, fixed timeouts, bounded
output, actor `extension:fab7hq/denim`, subject kind `denim-ask-batch`, the
batch ID as ref, and the canonical batch digest. Fab7 runs the installed
`denim verify-batch` command, which rechecks every ask/result digest and prior
seal exclusion. Denim accepts success only when the returned claim/evidence
and local ledger match the exact batch.

Failed and unsealed receipts remain immutable and may be retried. They do not
remove an ask from the pending set or complete the user's unit of work. A later
successful receipt is the completion checkpoint; subsequent asks form the next
unit's batch.

## Trust boundary

The user owns unit scope and the decision to seal it. The host owns conversation
context, permissions, tools, installed capability availability, provenance
signals, and the capability's actual invocation. The
host skill maps only advertised ownership to `custom`, `builtin`, `plugin`, or
`fallback`; ambiguous plugin ownership stays `plugin`. Workspace configuration
is untrusted selection metadata and never changes origin. Denim owns only
validation, resolution, bounded prompt enrichment, exact result capture,
immutable batching, and its Fab7 call. Fab7 supplies integrity and provenance
evidence, not a correctness oracle.
