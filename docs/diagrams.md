# Denim system diagrams

These diagrams describe Denim's current explicit ask-and-seal architecture.
The host owns its live capability inventory, permissions, and native capability
invocation. Denim owns validation, deterministic resolution, exact-result
capture, immutable batching, and the single bounded Fab7 seal call.
The user owns the unit of work, may issue multiple asks within it, and decides
that it is done by invoking seal.

## System architecture

```mermaid
flowchart LR
    subgraph client ["User"]
        developer["User / work owner"]
    end
    subgraph gateway ["Host Runtime"]
        hostAgent["Claude Code or Codex"]
    end
    subgraph service ["Local Execution"]
        denimSkills["Denim ask and seal skills"]
        denimExecutable["Denim executable"]
        fab7Executable["Fab7 executable"]
    end
    subgraph datastore ["Workspace State"]
        resolutionConfig["User config.toml and workspace .denim.toml"]
        denimArtifacts[".fab7/denim immutable artifacts"]
        fab7Ledger[".fab7/records append-only ledger"]
    end
    subgraph external ["Live Host Capabilities"]
        selectedCapability["Selected callable capability"]
    end

    developer -->|"Explicit ask or seal"| hostAgent
    hostAgent -->|"Runs installed skill"| denimSkills
    denimSkills -->|"Prepare, record, or seal"| denimExecutable
    denimSkills -.->|"Invoke once and capture"| selectedCapability
    denimExecutable -->|"Read overlays"| resolutionConfig
    denimExecutable -->|"Commit and verify"| denimArtifacts
    denimExecutable -->|"One bounded seal call"| fab7Executable
    fab7Executable -->|"Append evidence"| fab7Ledger
```

The Denim executable is one local program. Internally, `cli.py` dispatches to
the ask or seal path; discovery and configuration feed the deterministic
resolver; the store validates and atomically commits immutable artifacts; and
the Fab7 adapter owns the only Fab7 subprocess.

## Ask sequence

```mermaid
sequenceDiagram
    title Denim ask lifecycle
    participant Developer
    participant HostSkill
    participant HostRuntime
    participant Denim
    participant Resolver
    participant Capability
    participant ArtifactStore

    Developer->>HostSkill: Invoke explicit Denim ask
    HostSkill->>HostRuntime: Read live inventory
    HostRuntime-->>HostSkill: Descriptors and authority
    HostSkill->>Denim: Submit prepare request
    Denim->>Resolver: Validate, filter, and rank
    Resolver-->>Denim: Capability and bound ticket
    Denim-->>HostSkill: Ticket and enriched prompt
    HostSkill->>HostRuntime: Invoke selected capability
    HostRuntime->>Capability: Send enriched prompt once
    Capability-->>HostRuntime: Return exact final result
    HostRuntime-->>HostSkill: Capture bounded result
    HostSkill->>Denim: Submit record request
    Denim->>ArtifactStore: Commit result and ask record
    Denim-->>HostSkill: Return status and ask ID
    HostSkill-->>Developer: Report result; unit remains open
```

Capability selection is deterministic after the host supplies the snapshot:

1. Keep only visible, callable, result-capturable capabilities whose required
   authorities were granted.
2. Honor an eligible exact `use` ID, then an eligible configured `route`.
3. Rank specific positive matches by configured origin order, prompt-token
   overlap with scoped `provides`, priority, and capability ID.
4. Only when no specific candidate matches, rank `*` wildcard fallbacks by
   origin order, priority, and capability ID.
5. If nothing matches, commit `capability_unavailable` during preparation and
   do not invoke a capability.

The default origin order is custom business behavior, host built-in,
third-party or provenance-ambiguous plugin, then generic native fallback.
Eligibility and pins remain stronger than that preference.

## Seal sequence

```mermaid
sequenceDiagram
    title User completes a unit of work
    participant Developer
    participant SealSkill
    participant Denim
    participant ArtifactStore
    participant Fab7
    participant BatchVerifier
    participant Fab7Ledger

    Developer->>SealSkill: Declare unit done with Denim seal
    SealSkill->>Denim: Run denim seal once
    Denim->>ArtifactStore: Lock and snapshot unit's pending asks
    ArtifactStore-->>Denim: Return asks and prior batches
    Denim->>ArtifactStore: Commit or reuse immutable batch
    Denim->>Fab7: Seal batch ID and digest
    Fab7->>BatchVerifier: Run denim verify-batch
    BatchVerifier->>ArtifactStore: Recheck exact bindings
    BatchVerifier-->>Fab7: Return verified batch
    Fab7->>Fab7Ledger: Append claim and evidence
    Fab7Ledger-->>Fab7: Return exact record lines
    Fab7-->>Denim: Return bounded JSON receipt
    Denim->>Fab7Ledger: Validate receipt bindings
    Denim-->>SealSkill: Return sealed status and receipt
    SealSkill-->>Developer: Report sealed unit evidence
```

A successful seal proves integrity and provenance for the exact recorded ask
outcomes and records the user's completion boundary for that unit. It does not
prove that a model-authored result is semantically correct. Failed or unsealed
batches remain pending, so the unit is not sealed and may be retried.
