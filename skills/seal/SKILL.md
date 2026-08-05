---
name: seal
description: Explicitly seal every currently pending Denim ask in this workspace as one exact Fab7-bound batch. Use only when the user explicitly invokes {{invocation}}.
disable-model-invocation: true
---

This is an explicit checkpoint, not conversation finality.

1. Resolve the current initialized workspace. Do not infer a conversation ID,
   inspect arbitrary transcripts, or select individual asks.
2. Run `denim seal --workspace <workspace> --json` exactly once. Do not call
   Fab7 separately; the Denim executable owns the one bounded public Fab7
   subprocess.
3. If the result is `nothing_to_seal`, report it exactly. If sealing fails,
   report the exact failure and state that every ask remains pending.
4. On success, report the batch ID and digest, ask count, Fab7 claim and
   evidence IDs, and provenance. Describe the result only as integrity and
   provenance evidence for those exact ask outcomes. It is not proof that any
   model-authored result is semantically correct.
5. Stop. A later explicit ask begins the next pending batch; this conversation
   remains host-owned and usable.
