# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This is a monorepo containing multiple independent projects. Treat each top-level project folder as an independent project unless the user explicitly requests a cross-project change.

## Projects

- `gmail-mcp/`: Gmail MCP assistant project.
- `Job Hunting Automation/`: Job hunting automation project.

## Instruction Hierarchy

- Follow this root `CLAUDE.md` for all work in `Master_Repo`.
- If a project folder contains its own `CLAUDE.md`, read and follow it for that project.
- Project-level instructions add project-specific rules. If there is a conflict, ask the user before proceeding.

## Commit Guardrails

1. Before staging or committing, run `git status --short`.
2. Identify which top-level project folders have changes.
3. If more than one project has changes, ask the user which project to stage or commit.
4. Never run `git add .` from the monorepo root unless the user explicitly asks for a cross-project commit.
5. Prefer folder-scoped staging:
   - `git add gmail-mcp/`
   - `git add "Job Hunting Automation/"`
6. Before committing, show the staged files grouped by project.
7. Confirm the active branch before committing.
8. Use project-prefixed branch names:
   - `gmail-mcp/<feature-name>`
   - `job-hunting/<feature-name>`
9. Do not delete, reset, checkout, stash, or revert user changes unless explicitly requested.

## Private File Guardrails

Never stage, print, summarize, commit, or push:

- Credentials.
- OAuth tokens.
- API keys.
- `.env` files.
- Cache files.
- Generated private outputs.
- Personal mailbox data.
- Local-only notebooks or notebook outputs containing private data.

If a private/generated file appears in `git status`, warn the user and suggest `.gitignore` updates.

## Push and PR Guardrails

1. Push only committed changes.
2. Before pushing, confirm the branch and remote.
3. Do not push unrelated project changes.
4. Prefer PRs into `main` for project feature branches.
5. After a PR is merged, fetch/pull only when it is safe for the working tree.

## Communication Rules

1. State what project is being worked on.
2. State what files are being staged or committed.
3. State what remains uncommitted.
4. Ask before doing anything that may affect multiple projects.
