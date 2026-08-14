# Working in this repo

See [README.md](README.md) for what fragmint does and how to use it. This
file covers what an agent needs to know to change it.

## Conventions

- Shared agent context (instructions, house rules, prompts, and skills) lives
  in `AGENTS.md` and `.agents/skills/`, never duplicated into a
  harness-specific directory (`.claude/`, `.cursor/`, `.pi/`, `.github/`, or
  another harness directory). Load the `agent-context` skill before creating,
  editing, or relocating agent-facing files.
- Preserve the README as the authoritative product documentation. It must
  describe the tool for its users, not the development conversation.
- Use `apply_patch` for focused edits and keep unrelated working-tree changes
  intact.

## Development

Run the complete checks before handing off changes:

```bash
uv run pytest
uv run mypy src
uv run ruff check .
```

Keep `uv.lock` synchronized with `pyproject.toml`. Tests use stubbed command
runners; ordinary test runs must not invoke real subprocesses.

## Layout

- `src/fragmint/` — the package and bundled schemas.
- `tests/` — unit, integration, snapshot, and CLI tests.
- `example/` — a complete Ubuntu autoinstall and Ansible example project.
- `.github/workflows/ci.yml` — the Python 3.12/3.13/3.14 CI matrix.

## Workspace

`.agents/workspace/` holds material that supports development without being
part of fragmint itself:

- `followup/tasks.md` and `followup/inbox.md` — the development task queue,
  managed by the `followup` skill.
- `MEMORY.md` — durable development facts kept as a short reference.
- `plans/` — plans for larger pieces of work.
