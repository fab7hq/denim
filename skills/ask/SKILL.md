---
name: ask
description: Delegate one exact query through one currently discovered host capability, capture its exact bounded result, and leave it pending for an explicit Denim seal. Use only when the user explicitly invokes {{invocation}}.
disable-model-invocation: true
---

Treat all text after the explicit invocation as the exact ask. If it is empty,
ask the user for it and stop. Do not activate this workflow for an ordinary
prompt.

1. Read the current {{host}}-provided capability inventory and current callable
   tool list. Build one live snapshot using the descriptor schema in
   `references/protocol.md`. Include only capabilities advertised by the host
   in this turn. A generic native-agent fallback is eligible only when the host
   currently exposes it as callable and its final result is capturable. Apply
   these classifications only from provenance already advertised by the host:
   owner-, user-, or workspace-authored business behavior is `custom`; behavior
   shipped by the host is `builtin`; a third-party plugin, or any plugin whose
   owner-authored status is ambiguous, is `plugin`; and the generic native agent
   is `fallback`. A plugin namespace proves packaging, not owner authorship.
   Do not infer ownership from a name or path and do not reclassify a configured
   or exact pin.
2. Use scoped host-advertised `provides` values for specific capabilities.
   Treat `*` only as generic fallback behavior; it must not make a broad custom
   helper relevant to every ask. A custom multi-step workflow is one atomic
   host capability, normally `kind: skill`: invoke it once and capture only its
   final result, never its internal plan, tool calls, agents, or intermediate
   state.
3. Do not scan plugin caches or configuration directories, import candidate
   code, execute a candidate as a probe, type into an interactive composer,
   launch another host CLI, invent a slash command, or add authority.
4. Create a uniquely named temporary directory in the operating-system temp
   location, outside project source. Write the exact prepare request described
   in `references/protocol.md`; do not place credentials, transcripts, or
   unrelated context in it.
5. Run `denim ask --workspace <workspace> --request <prepare-file> --json`
   exactly once. If it returns `capability_unavailable`, present that exact
   outcome and stop. If it fails validation, present the exact failure and
   stop.
6. Invoke only the returned capability, exactly once, through its supported
   host-native mechanism. Pass the returned `enriched_prompt` without adding
   hidden requirements. Stay inside the user's current host permission and
   tool boundary.
7. Capture only the capability's bounded final result. Use `complete` when the
   capability returned a result and `blocked` when it truthfully stopped on an
   authority or execution boundary. Record explicit limitations and material
   effects; do not claim a transcript, chain of thought, or manually invoked
   command as the result.
8. Write the exact record request described in `references/protocol.md`, then
   run `denim ask --workspace <workspace> --request <record-file> --json`
   exactly once. Remove only the temporary directory created in step 4.
9. Return the exact result followed by the ask ID, selected capability ID,
   truthful status, and `Seal: pending`.

Never call Fab7 from this skill. Sealing is owned only by the separate explicit
Denim `seal` skill.
