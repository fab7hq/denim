# Contributing to Denim

Denim is maintained by a solo founder. Keep contributions small, host-neutral,
and within the `ask` and `seal` product boundary.

## Before writing code

- Use [Fab7 Discussions](https://github.com/fab7hq/fab7/discussions) for support
  and design proposals.
- Search existing Denim issues before filing a reproducible defect.
- Do not turn Denim into a workflow engine, transcript recorder, or replacement
  for the host agent.

## Development

```bash
uv sync --python 3.14.6
uv run --python 3.14.6 python -m pytest
git diff --check
```

Describe the exact host boundary affected, tests run, and any limitation or
material effect. Never include credentials, private prompts, or workspace ask
records.

By submitting a contribution, you agree that it is licensed under the
[Apache License 2.0](LICENSE).
