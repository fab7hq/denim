# Host packages and owner-run journeys

Denim builds one package for Claude Code and one for Codex. Both contain the
same explicit-only `ask` and `seal` skills and call the same installed `denim`
executable. Host packaging and native invocation differ, but Denim documents
one host-neutral operation vocabulary: `denim:ask` and `denim:seal`.

## Capability discovery rules

The `ask` skill uses only the current host-provided capability inventory and
callable tool list. It may describe an advertised built-in, installed skill,
plugin capability, callable tool or API, agent profile, or generic native-agent
fallback. Every descriptor states visibility, callability, result capture, and
required authority truthfully.

Classification uses only provenance already exposed by the current host:

| Advertised ownership | Denim origin |
| --- | --- |
| Owner, user, or workspace business capability | `custom` |
| Capability shipped and owned by the active host | `builtin` |
| Third-party plugin or ambiguous plugin ownership | `plugin` |
| Generic native agent | `fallback` |

A plugin namespace proves a delivery container, not owner authorship. A
plugin-delivered capability is `custom` only when the live host surface also
establishes owner, user, or workspace provenance. Otherwise it stays `plugin`;
an exact `use` or configured route may select it but never reclassifies it.
Names, writable paths, and cached manifests are not provenance.

Specific business behavior advertises scoped `provides` values. `*` means
generic fallback and is considered only after every specific positive match.
Eligibility is evaluated before exact pins, routes, and ranking.

The skill must not scan plugin caches, read arbitrary installation files,
execute candidates as probes, automate an interactive composer, launch a
nested Claude or Codex process, or claim a UI-only action is model-callable.
Codex custom behavior is invoked as a skill, plugin tool, agent, or supported
App Server capability—not as an invented slash command.

Claude Code exposes model-callable skills through its native Skill surface;
plugin skills are namespaced. Codex exposes live skills and enabled state and
accepts skills as native turn inputs. In either host, a user-only UI command is
not callable by Denim. A multi-step custom workflow is one advertised
`kind: skill` capability, one outer invocation, and one captured final result;
the host retains its internal plan, tools, agents, and intermediate state.

Current host metadata may still omit trustworthy owner provenance or a
capturable invocation surface. Such a candidate remains `plugin` or
ineligible. Unit tests and package validation cannot remove that limitation;
only the authenticated journeys below establish real-host behavior.

## Owner-run acceptance

For each host, install the freshly built package only with separate owner
authority, start a new session, and advertise one scoped owner-authored
workspace security skill alongside a relevant built-in. Then verify:

1. A covered security ask selects the exact custom ID.
2. An unrelated ask does not let that custom skill shadow a specific built-in
   or native fallback.
3. Removing required authority makes the custom candidate ineligible.
4. Omitting the custom ID from the next live snapshot does not synthesize it.
5. A custom workflow has one outer invocation and one bounded recorded final
   result with truthful limitations and effects.
6. `seal` binds the exact resulting ask records and results through Fab7 audit.

## Unified operation flow

After the owner installs the appropriate host package and starts a fresh
session, use the host's normal skill trigger for these host-neutral operations:

```text
denim:ask Review this authentication change for security concerns

Explain finding 2 in simpler language
Make the recommended code change

denim:ask Re-review the resulting authentication implementation
denim:seal
```

The user owns this unit of work. Both asks remain pending in the same unit while
ordinary prompts and native host commands remain host-owned. Each Denim ask
reports the selected live capability ID, exact status, and `Seal: pending`.
When the user decides the work is done, `denim:seal` closes the unit's exact
evidence set; it does not end the host conversation. The next ask begins the
next unit. Denim does not add host command enum values.

These are owner-run authenticated integration checks. Unit and package tests
can prove deterministic contracts, source inventory, and generated package
shape, but they must not be reported as real host invocation evidence.
