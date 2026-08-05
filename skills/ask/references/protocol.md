# Ask protocol

The host skill supplies live discovery; the Denim executable validates,
resolves, binds, and stores it. Both files are temporary UTF-8 JSON objects.

## Prepare

Every field is required. Use JSON `null` when `route` or `use` is absent.

```json
{
  "phase": "prepare",
  "host": "codex",
  "prompt": "exact user text",
  "candidates": [],
  "authority": [],
  "prohibitions": [],
  "target": "current workspace",
  "route": null,
  "use": null
}
```

For Claude, `host` is `claude`; for Codex, it is `codex`. Each candidate has
exactly these fields:

```json
{
  "id": "agent:native",
  "host": "codex",
  "origin": "fallback",
  "kind": "agent",
  "description": "Delegate a bounded task to one native agent",
  "provides": ["*"],
  "visible": true,
  "callable": true,
  "result_capture": true,
  "required_authority": [],
  "priority": 0
}
```

`origin` is a preference and ownership class:

- `custom` is owner-, user-, or workspace-authored business behavior, including
  behavior delivered through a plugin when the current host provides a
  trustworthy owner-provenance signal;
- `builtin` is behavior shipped and owned by the current host;
- `plugin` is third-party behavior and any plugin behavior whose owner status is
  not established by the live host signal; and
- `fallback` is the generic native-agent capability.

Plugin packaging or a suggestive identifier does not establish owner
authorship. Do not inspect a path or cache to decide origin. An exact `use` or
configured route may select an eligible ambiguous plugin, but does not
reclassify it.

`kind` is `agent`, `api`, `command`, `skill`, or `tool`. Until the host exposes
a distinct supported workflow kind, an owner-authored workflow is one
`kind: "skill"`, `origin: "custom"` descriptor. Its internal steps remain
host-owned; the Ask boundary receives one invocation and one final result.

A visible but user-only command remains truthfully `callable: false`. Do not
supply `metadata_digest`; Denim computes it. `provides` is open discovered
metadata, not a Denim scenario enum. Specific business capabilities advertise
scoped values such as `workspace-security-review`. `*` means generic fallback
and is considered only when no eligible candidate has a specific positive
match. A capability may advertise both scoped values and `*`; a matching scoped
value remains specific.

The response status is either `ready` with one `capability`, one
`enriched_prompt`, and one `ticket`, or a stored `capability_unavailable`
outcome. Treat the ticket as opaque: copy it byte-for-byte as a JSON value into
the record request.

## Record

Every field is required:

```json
{
  "phase": "record",
  "ticket": {},
  "status": "complete",
  "result": "exact capability result",
  "limitations": [],
  "effects": []
}
```

`status` is `complete` or `blocked`. `limitations` and `effects` contain only
short truthful strings about this invocation. The result may contain newlines;
JSON escaping changes no decoded text.
