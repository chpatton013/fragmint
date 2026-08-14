# MEMORY

Agent-owned list of durable reference facts about developing in this repo.
Keep it short; it is a quick reference, not an archive.

## Facts

- Run `uv run pytest`, `uv run mypy src`, and `uv run ruff check .` before handoff.
- The example project is a regression corpus; committed snapshot fixtures should
  remain byte-identical unless a behavior change explicitly requires new ones.
