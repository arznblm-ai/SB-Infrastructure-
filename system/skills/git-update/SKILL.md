---
name: git-update
description: Safely sync Anton's Second Brain infrastructure changes into the private SB-Infrastructure Git repository, run guardrail scans for secrets/private data/runtime folders, create a clean git commit, and optionally push when terminal GitHub auth works. Use when Anton asks to update Git, commit infrastructure changes, sync Second Brain infrastructure to GitHub, make a safe infra snapshot, or "занеси изменения инфраструктуры в git".
metadata:
  short-description: Safely commit Second Brain infrastructure changes to Git
model: haiku
---

# Git Update

### [[2026-07-05]]

Use this skill to snapshot Anton's reusable Second Brain infrastructure into the private Git repo:

`/Users/anton/AI AGENT FOLDER/SB-Infrastructure-`

It takes whitelisted infrastructure files from the vault, mirrors them into that repo, runs secret/private-data safety scans, and produces a clean commit; push stays on GitHub Desktop unless Anton explicitly asks for terminal push.

The skill must never commit the full Second Brain. It only syncs a whitelist of reusable infrastructure files and runs safety checks before committing.

## What Gets Synced

- `system/skills/`
- `infrastructure/Research Dept/SKILL.md`
- `infrastructure/Research Dept/references/`
- `infrastructure/Research Dept/templates/`
- `infrastructure/Research Dept/scripts/`
- `infrastructure/Research Dept/skills/`
- `infrastructure/Research Dept/agents/`
- safe source code from `infrastructure/Personal OS/`
- safe docs/config from `infrastructure/Link Inbox/`
- safe docs from `infrastructure/Git Update/`
- `*/Scripts/` folders under `infrastructure/` into `infrastructure/automation-scripts/`
- Codex session exporter script from `sessions/codex/Scripts/`

## What Must Stay Out

- `meetings/`
- `transcripts/`
- `sessions/` data
- Research Dept `workspace/`
- raw exports, outputs, runs, logs, app data
- `.env`, tokens, credentials, private keys
- Personal OS seed/import files with real financial/client data
- `node_modules`, `.next`, `.tools`, caches

## Standard Workflow

Run the bundled script first:

```bash
python3 "/Users/anton/.codex/skills/git-update/scripts/sync_infrastructure_repo.py"
```

The script will:

1. Sync whitelisted infrastructure into `SB-Infrastructure-`.
2. Run safety scans.
3. Show changed files.
4. Commit with a generated message if safe.
5. Leave push to GitHub Desktop unless `--push` is explicitly used and terminal auth works.

For a preview without writing:

```bash
python3 "/Users/anton/.codex/skills/git-update/scripts/sync_infrastructure_repo.py" --dry-run --no-commit
```

For a custom commit message:

```bash
python3 "/Users/anton/.codex/skills/git-update/scripts/sync_infrastructure_repo.py" --message "Update Research Dept and skills"
```

For terminal push, only when auth is known to work:

```bash
python3 "/Users/anton/.codex/skills/git-update/scripts/sync_infrastructure_repo.py" --push
```

## Response Rules

After running, tell Anton:

- whether a commit was created;
- commit hash and message;
- whether push happened or needs GitHub Desktop;
- any safety scan failure, with exact file paths;
- if there were no changes.

Do not push automatically unless Anton explicitly asks and terminal Git auth works.
