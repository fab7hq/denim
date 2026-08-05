# Denim overview

Denim has two explicit operations: `ask` and `seal`.

## User-owned unit of work

The user owns the unit-of-work boundary. A unit may contain multiple
`denim:ask` operations together with ordinary prompts, native host commands,
and implementation work that Denim does not intercept. Each Denim result stays
pending in the workspace as part of the current unit's evidence set.

The user decides when the unit is done by invoking `denim:seal`. A successful
seal closes the pending evidence set for that unit without ending the host
conversation; a later ask begins the next unit. Denim does not create a
workflow object, decide completion criteria, or manage the work between asks.

## Ask

`ask` delegates the exact user prompt to at most one capability advertised by
the current Claude or Codex session. The host skill supplies a live descriptor
snapshot; the Denim executable validates, configures, and resolves it without
reading host caches or executing discovery probes.

An eligible capability is visible, callable by the model, able to return a
capturable final result, relevant to the query, and within authority already
granted by the user and host. The closed ask statuses are:

- `complete`: the selected capability returned its final result;
- `blocked`: the selected capability stopped on an authority or execution
  boundary; and
- `capability_unavailable`: no eligible live capability could be selected.

Every outcome is immutable and pending until the user seals the unit of work.
Ordinary host prompts and manually invoked commands are neither intercepted
nor recorded.

## Resolution configuration

Configuration precedence is the built-in policy, user
`~/.config/denim/config.toml`, workspace `.denim.toml`, then an exact option for
the current ask. Each file is schema 1 TOML:

```toml
version = 1

[resolution]
order = ["custom", "builtin", "plugin", "fallback"]

[routes.security_review]
use = "skill:acme-security-review"
allow_fallback = false

[[capabilities]]
id = "skill:acme-security-review"
provides = ["security_review"]
priority = 50
```

`custom` means owner-, user-, or workspace-authored business behavior, even
when a supported live provenance signal shows that it is delivered through a
plugin. Host-owned behavior is `builtin`. Third-party or provenance-ambiguous
plugin behavior remains `plugin`; packaging and names alone do not prove
ownership. `fallback` is the generic native agent.

Route names and `provides` values are open discovered metadata, not a Denim
scenario enum. Configuration can change only the order, route pin,
`provides`, and priority of IDs present in live discovery. Unknown fields,
command text, callability flags, symlinked files, and malformed values fail
closed.

Resolution filters eligibility before every pin or preference. It then honors
an exact `use`, a configured route, specific positive matches, and finally
wildcard candidates. Within a specific origin tier, stronger match wins, then
higher priority, then lexical ID. `*` is generic fallback behavior and never
shadows a specific match. An explicit order can restore built-in-first behavior
for a user or workspace.

## Prompt and result

Denim preserves the exact ask and adds only:

```text
Exact ask: <verbatim text>
Target: <workspace or explicit target>
Authority: <current granted boundary>
Result contract: <selected capability's capturable result>
Stop conditions: <user prohibitions and capability failure conditions>
```

The host skill invokes the selected capability once and records only its final
result, limitations, and material effects. A custom multi-step workflow remains
one host-owned atomic skill; Denim stores neither its plan nor intermediate
state. Credentials, environment dumps, chain of thought, unrelated
interactions, and arbitrary transcripts are not Denim data.

## Seal

`seal` is the user's explicit declaration that the current unit of work is
done. It holds the workspace seal lock, derives asks absent from valid prior
Fab7-sealed batches, writes one canonically ordered immutable receipt, and
calls public `fab7 seal` once. A failed, timed-out, malformed, or mismatched
Fab7 call leaves every ask pending, so the unit is not sealed. An ask written
after the snapshot belongs to the next unit.

A successful seal reports the exact batch digest, Fab7 claim and evidence IDs,
and provenance. It is evidence that the recorded verifier passed for those
exact bytes, not a semantic correctness verdict.
