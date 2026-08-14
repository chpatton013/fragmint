# Workspace

This directory serves as the Agent's workspace. Anything that doesn't belong
as part of fragmint, but is worth persisting for agentic development, should
live here.

## What belongs here

- `followup/tasks.md` — the agent-owned development task list.
- `followup/inbox.md` — the drop box for new tasks and notes, moved into
  `tasks.md` by the `followup` skill.
- `MEMORY.md` — durable facts about developing in this repository.
- `plans/` — plan documents for larger development tasks.
- Scratch drafts and intermediate analyses that inform work without being the
  work itself.

Prefer committing meaningful intermediates with a message explaining the idea
behind them, so the git log remains useful for understanding development.
