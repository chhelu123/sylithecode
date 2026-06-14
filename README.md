# BharatCode 🇮🇳

**AI Coding Agent for Indian Developers — powered by DeepSeek**

BharatCode is a terminal-based AI coding agent that understands Indian tech stacks (Razorpay, UIDAI, GST, UPI), runs tools autonomously, and orchestrates parallel specialist workers for complex tasks.

---

## Features

- **Autonomous agent loop** — reads files, writes code, runs bash, fixes tests end-to-end
- **Coordinator mode** — spawns parallel background workers, synthesizes results in one shot
- **Plan mode** — read-only exploration + approval gate before any writes
- **Persistent memory** — remembers project facts across sessions
- **Project symbol index** — scans codebase at session start, injects file/symbol map into context
- **Grep-before-Read discipline** — built-in rule to grep for symbols before cold-reading files
- **Auto-read before edit** — auto-fetches file into cache before any edit_file call
- **old_string verification** — edit_file fails fast if the string isn't found or is ambiguous
- **Post-write syntax check** — Python AST parse after every write/edit; flags errors immediately
- **Context-aware compaction** — file knowledge (paths, symbols, sizes) survives auto-compaction
- **Change tracking** — tracks every file touched, shows diff summary on `/changes` and on exit
- **Auto-compaction** — summarises long sessions to stay under context limits
- **Indian expertise** — Razorpay, Aadhaar/UIDAI, GST, UPI, IndiaStack built-in knowledge
- **Cost tracking** — per-session token and rupee cost display

---

## Installation

```bash
git clone <repo>
cd bharatcode
pip install -e .
```

Set your DeepSeek API key:

```bash
# .env in any project directory, or export globally
DEEPSEEK_API_KEY=sk-...
```

---

## Usage

```bash
# Start interactive mode in your project directory
cd /path/to/your/project
bharatcode

# One-shot task
bharatcode run "add input validation to the signup form"
```

### Key Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/coordinator` | Enter multi-worker orchestration mode |
| `/plan` | Toggle plan mode (read-only until approved) |
| `/compact` | Summarise conversation history |
| `/changes` | Show all files modified this session |
| `/cost` | Show session token + cost breakdown |
| `/memory` | View persistent memories |
| `/workers` | Show coordinator worker status |
| `/exit-coordinator` | Leave coordinator mode |

---

## Project Config

Create a `BHARATCODE.md` file in any project to give BharatCode custom instructions for that project:

```markdown
# My Project — BharatCode Config

## Tech Stack
Python 3.11 / FastAPI / PostgreSQL / React 18

## Coding Standards
- Use Black for formatting
- Type hints on all functions
- Run pytest after every fix

## Notes
- Never commit .env files
- Payments via Razorpay v2
- GST: 18% on services
```

---

## Architecture

```
bharatcode/
  main.py         CLI entry + interactive loop + coordinator notification loop
  agent.py        Core agent loop — streaming, tool execution, auto-compaction
  coordinator.py  WorkerPool, Worker dataclass, notification queue, prompts
  tools.py        Tool definitions (JSON schema) + execute_tool dispatcher
  commands.py     Slash command handlers (/plan, /memory, /coordinator, ...)
  memory.py       Persistent memory read/write (~/.bharatcode/memory/)
  permissions.py  Tool approval — ask_permission, needs_approval
  subagent.py     spawn_agent tool — single-use focused sub-agents
  project.py      Auto-detect project type from package.json / requirements.txt
  config.py       Load/save ~/.bharatcode/config.json
  cost.py         Token + cost tracking per session
  diff.py         Coloured file diff display on write/edit
  hooks.py        Pre/post tool hooks for extensibility
  skills.py       /skill command — load skills from ~/.bharatcode/skills/
  ui.py           Rich console helpers — banner, panels, errors
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPSEEK_API_KEY` | ✅ | DeepSeek API key |
| `BHARATCODE_MODEL` | No | Override model (`deepseek-v4-pro` / `deepseek-v4-flash`) |
| `BHARATCODE_DEBUG` | No | Set to `1` for full tracebacks |
| `BHARATCODE_AUTO_APPROVE` | No | Set to `1` to skip all permission prompts |
