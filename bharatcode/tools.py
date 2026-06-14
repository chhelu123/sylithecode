import os
import subprocess
import glob as _glob
import re
from pathlib import Path
from typing import Any

BLOCKED_COMMANDS = ["rm -rf /", "format c:", "DROP TABLE", "sudo rm -rf", "mkfs", ":(){:|:&};:"]

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".idea"}

def _read_text_safe(p: Path) -> str:
    """Read text tolerantly: UTF-8 first, then cp1252 (common on Windows),
    finally UTF-8 with replacement so a stray byte never crashes a task."""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return p.read_text(encoding="cp1252")
        except (UnicodeDecodeError, LookupError):
            return p.read_text(encoding="utf-8", errors="replace")

# ── Tool Definitions (OpenAI-compatible function calling format) ──────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Execute any shell command and return its output. "
                "Use for: running tests, installing packages (pip/npm/yarn), git operations, "
                "building projects, checking system state, creating directories. "
                "Runs in Windows cmd.exe — use cmd syntax: chain with &&, use dir/type/mkdir/del "
                "(NOT ls/cat/rm, NOT PowerShell cmdlets like Get-ChildItem). "
                "For PowerShell features wrap explicitly: powershell -NoProfile -Command \"...\". "
                "Output is capped at 8000 chars. Default timeout 60s — pass timeout=300 for "
                "installs, builds, test suites. "
                "SERVERS / LONG-RUNNING PROCESSES: pass run_in_background=true — returns a "
                "process_id immediately instead of blocking. Then use process_output to read "
                "its logs and process_kill to stop it. This is how you VERIFY a server starts. "
                "Examples: 'pip install flask', 'git status', 'python manage.py migrate', "
                "'npm run build', 'dir \"C:\\my project\"'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute. Use full paths for files with spaces."
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait (default 60). Use 300+ for installs, builds, tests. Ignored when run_in_background=true."
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "true = start the process in the background and return a process_id immediately. REQUIRED for servers (python app.py, npm run dev, uvicorn, vite) — they never exit, so a foreground call would hang."
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "process_output",
            "description": (
                "Read the accumulated output of a background process started with "
                "bash(run_in_background=true). Shows RUNNING/EXITED status plus the last "
                "lines of stdout+stderr. Use wait_seconds=4 after starting a server to give "
                "it time to boot before checking for errors/tracebacks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "The process_id returned by bash(run_in_background=true), e.g. 'proc-1'"
                    },
                    "wait_seconds": {
                        "type": "integer",
                        "description": "Seconds to wait before reading (max 30). Use 3-5 for server boot."
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": "How many trailing output lines to return (default 60)."
                    }
                },
                "required": ["process_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "process_kill",
            "description": (
                "Stop a background process (and its child processes) started with "
                "bash(run_in_background=true). ALWAYS call this on every server you started "
                "once verification is done — never leave processes running."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "process_id": {
                        "type": "string",
                        "description": "The process_id to stop, e.g. 'proc-1'"
                    }
                },
                "required": ["process_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file's contents with line numbers. Returns up to 2000 lines per call; "
                "the header shows the total line count and a notice tells you when the file "
                "continues — call again with offset= to read the rest. Most files fit in one call. "
                "RULES: "
                "(1) 'path' must be a full file path like 'C:/project/src/app.py' — never a directory, never '.', never a bare number. "
                "(2) Repeat reads are served instantly from the session cache — re-read whenever you are "
                "not 100% sure of a file's CURRENT exact content (e.g. right before edit_file). "
                "(3) You MUST read a file before you can edit_file it — edits on unread files are blocked. "
                "(4) Check saved memories first — if the file's contents are already described there, skip reading it. "
                "Use offset/limit for targeted ranges (e.g. grep gave you a line number)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute file path, e.g. 'C:/chhelu 1/analysis/site3_report.html' or 'C:/project/backend/app.py'"
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line number to start from (0-indexed, default 0 = start of file). NOT a file path."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to return (default 2000). Use with offset for a specific range, e.g. offset=190, limit=40."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file (create or overwrite). Use for small-to-medium files (<150 lines). "
                "WARNING: For large files (HTML dashboards, full reports, >150 lines), do NOT use this tool — "
                "use the <<<FILE:path>>> marker in your response text instead (avoids JSON truncation). "
                "RULES: "
                "(1) 'path' must be the full file path WITH filename — e.g. 'C:/project/app.py'. Never '.', never a directory. "
                "(2) mode='w' creates/overwrites (default). mode='a' appends to existing file. "
                "(3) Parent directories are created automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full absolute file path including filename, e.g. 'C:/project/src/utils.py'"
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write. For files >150 lines, use <<<FILE:path>>> in response text instead."
                    },
                    "mode": {
                        "type": "string",
                        "description": "'w' to create/overwrite (default), 'a' to append to existing file without erasing it"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace an exact string in an existing file — surgical, targeted edits only. "
                "Perfect for: fixing a bug on 2-3 lines, updating a config value, changing a function signature. "
                "ENFORCED: you must have READ the file this session before editing it, and if the file "
                "changed on disk after your last read the edit is blocked until you re-read. "
                "WORKFLOW: "
                "(1) grep for the function/line you want to change → get the line number. "
                "(2) read_file(offset=LINE-5, limit=20) → get the exact text around that line. "
                "(3) Copy old_string VERBATIM from the read_file output — never type it from memory. "
                "(4) Call edit_file with that exact old_string. "
                "If edit_file returns 'not found': re-read that section, copy exact text, retry. "
                "NEVER write a helper script to do what edit_file should do. "
                "RULES: "
                "(1) 'path' = full file path with filename. Never '.', never a directory. "
                "(2) 'old_string' must appear EXACTLY ONCE in the file — include enough surrounding lines to make it unique. "
                "(3) If old_string appears 0 times → re-read the file, copy exact text, retry. "
                "(4) Use replace_all=true to rename a variable/function everywhere in the file. "
                "(5) Do NOT use for large rewrites — use <<<FILE:path>>> marker for that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full absolute file path, e.g. 'C:/project/src/routes.py'"
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to find and replace. Must be unique in the file. Include 2-3 lines of context if needed."
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement text. Can be empty string to delete old_string."
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "If true, replace ALL occurrences of old_string (default false replaces only the first). Use for renaming a variable or function across an entire file."
                    }
                },
                "required": ["path", "old_string", "new_string"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": (
                "Find files by name pattern using glob syntax. Returns matching paths sorted by modification time. "
                "Use when you need to discover files: find all Python files, all HTML reports, all config files. "
                "Examples: '**/*.py' (all Python files recursively), '*.html' (HTML in current dir), "
                "'src/**/*.ts' (TypeScript under src/), 'requirements*.txt' (requirements files). "
                "Skips: node_modules, .git, __pycache__, .venv, dist, build."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. '**/*.py', '*.html', 'src/**/*.ts', 'config/*.json'"
                    },
                    "directory": {
                        "type": "string",
                        "description": "Base directory to search from (default: current working directory)"
                    }
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search for a regex pattern inside files and return matching lines with line numbers. "
                "THIS IS YOUR ENTRY POINT FOR EVERY CODE FIX — always grep before read_file. "
                "grep tells you the exact file + exact line number so you never read blindly. "
                "Use when you need to: find where a function/class/variable is defined, find all usages of an API, "
                "search for a string across a codebase, locate error messages, find imports. "
                "Returns up to 100 matches with file path + line number + matching line content. "
                "Bug-fix workflow: grep for the function/string in the error → "
                "get line number → read_file(offset=LINE-5, limit=30) → edit_file with exact text. "
                "Examples: pattern='def authenticate' finds all auth function definitions; "
                "pattern='import.*pandas' glob='*.py' finds all pandas imports; "
                "pattern='UnicodeDecodeError' finds where encoding errors are handled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex or literal string to search for, e.g. 'def authenticate', 'TODO', 'api_key\\s*='"
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in (default: current directory)"
                    },
                    "glob": {
                        "type": "string",
                        "description": "Only search files matching this pattern, e.g. '*.py', '*.js', '*.html'"
                    }
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch and return the text content of any URL. Strips HTML tags for clean readable output. "
                "Use for: reading API documentation, Stack Overflow answers, GitHub READMEs, error pages, "
                "npm/PyPI package pages, official docs, any webpage with relevant information. "
                "Returns up to 8000 chars by default (increase max_chars for longer docs)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to fetch, e.g. 'https://docs.python.org/3/library/pathlib.html'"
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Max characters to return (default 8000). Use 20000+ for long documentation pages."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": (
                "List files and subdirectories with sizes. Use to explore project structure before diving in. "
                "Shows file sizes so you know what you're dealing with before reading. "
                "Use recursive=true to see the full tree (avoid on very large projects). "
                "Skips: node_modules, .git, __pycache__, .venv, dist, build."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (default: current working directory)"
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, lists all files and subdirs recursively. Default false (top-level only)."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": (
                "Create or update your live task checklist — REQUIRED for any task with 3+ steps "
                "(app builds, multi-file fixes, refactors). Pass the COMPLETE list every time "
                "(it replaces the previous list). "
                "WORKFLOW: (1) at the start, call todo with every step planned, first one "
                "in_progress; (2) the moment a step is done, call todo again marking it completed "
                "and the next one in_progress; (3) exactly ONE task in_progress at a time; "
                "(4) never end your turn with tasks still pending — finish them or tell the user why. "
                "The current list is re-shown to you every turn so you never lose track mid-build."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "The complete task list (replaces the previous list).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "Short imperative description, e.g. 'Write backend auth routes'"
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Current status of this task."
                                }
                            },
                            "required": ["content", "status"]
                        }
                    }
                },
                "required": ["tasks"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Save a fact to PERSISTENT MEMORY that survives across sessions. "
                "Call this at the end of every task — future sessions read these memories so you won't "
                "re-read files or re-discover context you already have. "
                "WHAT to save: files you created/edited (path + line count + what it does), "
                "key data extracted (metrics, config values, API keys location), "
                "project structure discoveries, user preferences, task outcomes, decisions made. "
                "BE SPECIFIC: include exact paths, numbers, and what the content is. "
                "BAD: remember('worked on dashboard') "
                "GOOD: remember('C:/chhelu 1/analysis/master_dashboard.html — 800 lines, Chart.js dark-theme, "
                "7 charts comparing 3 Sylithe forest sites, radar scorecard added June 2026', tag='file')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The specific fact to save. Include paths, numbers, outcomes. Be precise — vague memories are useless."
                    },
                    "tag": {
                        "type": "string",
                        "description": "Category tag: 'file' (created/edited files), 'data' (extracted numbers/metrics), 'project' (structure/stack), 'user' (preferences), 'task' (outcomes). Default: 'project'"
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": (
                "Spawn a specialized sub-agent to handle a subtask with its own isolated context, "
                "dedicated toolset, and purpose-built system prompt.\n\n"
                "WHEN TO USE:\n"
                "  • Before building: spawn explore to map the codebase so you understand it before writing\n"
                "  • During build: spawn researcher to fetch docs for an unknown library while you implement\n"
                "  • After building: spawn verifier to audit your code for bugs and security issues\n"
                "  • Complex subtask: spawn coder to handle a large isolated piece (e.g. auth module)\n"
                "  • Parallel work: call spawn_agent twice in one response — both run simultaneously\n\n"
                "AGENT TYPES — choose the right specialist:\n"
                "  explore    ← READ-ONLY analyst. Reads files, greps code, maps structure. Cannot write.\n"
                "               Use to understand existing code before making changes.\n"
                "  coder      ← FULL-ACCESS implementer. Reads, writes, runs bash. Ships complete code.\n"
                "               Use for isolated implementation tasks.\n"
                "  verifier   ← STRICT AUDITOR. Reads code and runs tests. Finds bugs + security holes.\n"
                "               Always spawn after implementing a feature before telling the user it's done.\n"
                "  researcher ← LIVE WEB FETCHER. Gets real docs, real examples, real API specs.\n"
                "               Use when you don't know an API exactly — don't guess, research it.\n"
                "  general    ← ALL TOOLS. No restrictions. Use when the task spans multiple roles.\n\n"
                "KEY FACTS:\n"
                "  - Subagents share your file_cache — no duplicate reads, zero wasted API calls\n"
                "  - Subagents have fresh history — write self-contained tasks with all needed context\n"
                "  - Subagents cannot spawn further agents — no runaway chains\n"
                "  - The agent's final response is returned as this tool's result"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Full, self-contained task for the subagent. Include all context it needs — "
                            "file paths, goal, constraints. The agent has no memory of your conversation."
                        ),
                    },
                    "agent_type": {
                        "type": "string",
                        "enum": ["explore", "coder", "verifier", "researcher", "general"],
                        "description": "Type of specialized agent to spawn.",
                    },
                },
                "required": ["task"],
            },
        },
    },
]

# Tools filtered for subagents — spawn_agent removed to prevent recursion
_TOOLS_NO_SPAWN = [t for t in TOOLS if t["function"]["name"] != "spawn_agent"]

# ── Coordinator-only Tools ────────────────────────────────────────────────────
# These 3 tools are ONLY given to the main agent when in coordinator mode.
# Workers never receive them (no recursive coordination).

COORDINATOR_ONLY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "spawn_worker",
            "description": (
                "Spawn an async specialist worker. Returns worker_id IMMEDIATELY — does not block.\n\n"
                "The worker runs in the background. When it completes, a <task-notification> XML "
                "message will appear in your conversation with its full output.\n\n"
                "PARALLELISM: Call spawn_worker multiple times in ONE response to run workers in parallel. "
                "This is your superpower — read-only workers (explore/researcher) can always run in parallel.\n\n"
                "WORKER TYPES:\n"
                "  explore    — read_file, glob, grep, list_dir only. Use for codebase mapping.\n"
                "  researcher — web_fetch + read_file. Use for docs, API specs, real examples.\n"
                "  coder      — ALL tools (read + write + bash). Use for implementation & commits.\n"
                "  verifier   — read_file, grep, bash. Use for testing & security audit.\n"
                "  general    — all tools. Use for multi-role tasks.\n\n"
                "CRITICAL: Write self-contained prompts — workers have NO memory of your conversation. "
                "Include file paths, line numbers, what 'done' looks like. "
                "For implementation: ask them to run tests and commit before reporting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Full self-contained task description. Must include: exact goal, "
                            "file paths and line numbers when known, what 'done' looks like. "
                            "For implementation tasks: 'Run tests, commit, report the commit hash.' "
                            "For research tasks: 'Report findings — do not modify files.'"
                        )
                    },
                    "agent_type": {
                        "type": "string",
                        "enum": ["explore", "coder", "verifier", "researcher", "general"],
                        "description": "Specialist type. Choose based on what the worker needs to do."
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "Short human-readable label shown in the UI and in the <task-notification> "
                            "summary. Examples: 'Auth bug investigation', 'Razorpay docs research', "
                            "'Fix null pointer in validate.py'. Max 60 chars."
                        )
                    }
                },
                "required": ["task"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": (
                "Send a follow-up message to an existing worker using its worker_id.\n\n"
                "Use to CONTINUE a worker — either it's still running (live injection) or "
                "it already completed and you want it to do the next phase (re-start with context).\n\n"
                "WHEN TO CONTINUE vs SPAWN FRESH:\n"
                "  Continue  — worker already explored the exact files that need editing\n"
                "  Continue  — correcting a worker's own test failures (it has the error context)\n"
                "  Spawn fresh — verifying code another worker wrote (fresh eyes)\n"
                "  Spawn fresh — broad research worker, narrow implementation task\n\n"
                "The worker_id comes from spawn_worker's result OR from <task-id> in a <task-notification>.\n\n"
                "Write a complete self-contained instruction. Reference what the worker did "
                "('the null check you added'), not what you discussed with the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "worker_id": {
                        "type": "string",
                        "description": "The worker_id from spawn_worker result or <task-id> in a notification."
                    },
                    "message": {
                        "type": "string",
                        "description": (
                            "Follow-up instruction. Be specific: include file paths, exact changes, "
                            "what 'done' looks like. Reference the worker's own actions when correcting "
                            "('the test failure at line 58'). Self-contained — the worker has context "
                            "from its prior run but NOT from your conversation with the user."
                        )
                    }
                },
                "required": ["worker_id", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_stop",
            "description": (
                "Send an abort signal to a running worker.\n\n"
                "Use when:\n"
                "  - You launched a worker in the wrong direction\n"
                "  - The user changed requirements after you launched the worker\n"
                "  - A worker is taking too long on an approach you've already ruled out\n\n"
                "The worker stops cleanly at the next safe boundary (not mid-write). "
                "A stopped worker CAN be continued with send_message — it keeps its history."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "worker_id": {
                        "type": "string",
                        "description": "The worker_id to stop."
                    }
                },
                "required": ["worker_id"]
            }
        }
    },
]

# Tools the coordinator uses directly (no write/bash — workers do that)
_COORDINATOR_READ_NAMES = {"read_file", "glob", "grep", "list_dir", "web_fetch", "remember"}

COORDINATOR_TOOLS = (
    COORDINATOR_ONLY_TOOLS
    + [t for t in TOOLS if t["function"]["name"] in _COORDINATOR_READ_NAMES]
)

# ── Tool Implementations ──────────────────────────────────────────────────────

# ── Background process registry ───────────────────────────────────────────────
# Lets the agent START servers, CHECK their output, and KILL them — the
# verify-by-running loop. Output goes to a log file so reading it never blocks.

_BG_PROCS: dict = {}
_BG_COUNTER = [0]


def _kill_proc_tree(proc) -> None:
    try:
        if os.name == "nt":
            # taskkill /T kills the whole tree (cmd → python/node children)
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _cleanup_bg_procs() -> None:
    """Never leave servers running after BharatCode exits."""
    for info in list(_BG_PROCS.values()):
        proc = info.get("proc")
        if proc is not None and proc.poll() is None:
            _kill_proc_tree(proc)


import atexit
atexit.register(_cleanup_bg_procs)


def _bash_background(command: str) -> str:
    import tempfile
    import time as _time
    _BG_COUNTER[0] += 1
    proc_id  = f"proc-{_BG_COUNTER[0]}"
    log_path = Path(tempfile.gettempdir()) / f"bharatcode_{os.getpid()}_{proc_id}.log"
    fh = open(log_path, "w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            command, shell=True, stdout=fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, cwd=os.getcwd(),
        )
    except Exception as e:
        fh.close()
        return f"Error starting background process: {e}"
    _BG_PROCS[proc_id] = {
        "proc": proc, "fh": fh, "log": str(log_path),
        "command": command, "started": _time.time(),
    }
    return (
        f"Started background process {proc_id} (system pid {proc.pid}).\n"
        f"Command: {command}\n"
        f"Next: process_output(process_id='{proc_id}', wait_seconds=4) to check its "
        f"boot logs, then verify (e.g. web_fetch the health endpoint), then "
        f"process_kill(process_id='{proc_id}') when you are done. "
        f"ALWAYS kill what you started."
    )


def bash(command: str, timeout: int = 60, run_in_background: bool = False) -> str:
    for blocked in BLOCKED_COMMANDS:
        if blocked in command:
            return f"Error: Blocked command '{blocked}'"
    if run_in_background:
        return _bash_background(command)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout, cwd=os.getcwd()
        )
        out = result.stdout
        if result.stderr:
            out += f"\nSTDERR:\n{result.stderr}"
        if result.returncode != 0:
            out += f"\n[exit code: {result.returncode}]"
        return out[:8000] or "(no output)"
    except subprocess.TimeoutExpired:
        return (
            f"Timed out after {timeout}s. If this is a server/long-running process, "
            f"use run_in_background=true instead. If it is a slow install/build, "
            f"retry with timeout=300."
        )
    except Exception as e:
        return f"Error: {e}"


def process_output(process_id: str, wait_seconds: int = 0, tail_lines: int = 60) -> str:
    info = _BG_PROCS.get(process_id)
    if info is None:
        active = ", ".join(_BG_PROCS) or "none"
        return f"Error: no background process '{process_id}'. Active processes: {active}"
    if wait_seconds:
        import time as _time
        _time.sleep(min(int(wait_seconds), 30))
    proc = info["proc"]
    code = proc.poll()
    status = "RUNNING" if code is None else f"EXITED (code {code})"
    try:
        info["fh"].flush()
    except Exception:
        pass
    try:
        content = Path(info["log"]).read_text(encoding="utf-8", errors="replace")
    except Exception:
        content = ""
    lines = content.splitlines()
    tail = "\n".join(lines[-int(tail_lines):])
    return (
        f"[{process_id}] {info['command']}\n"
        f"Status: {status}\n"
        f"Output (last {min(len(lines), int(tail_lines))} of {len(lines)} lines):\n"
        f"{tail or '(no output yet — a server may need a few seconds; retry with wait_seconds=4)'}"
    )


def process_kill(process_id: str) -> str:
    info = _BG_PROCS.get(process_id)
    if info is None:
        active = ", ".join(_BG_PROCS) or "none"
        return f"Error: no background process '{process_id}'. Active processes: {active}"
    proc = info["proc"]
    if proc.poll() is not None:
        return f"{process_id} had already exited with code {proc.returncode}."
    _kill_proc_tree(proc)
    try:
        info["fh"].close()
    except Exception:
        pass
    return f"Killed {process_id} and its child processes."

_READ_DEFAULT_LIMIT = 2000   # lines per call — protects context from giant files

def read_file(path: str, offset: int = 0, limit: int = None) -> str:
    if not path or not path.strip():
        return (
            "Error: 'path' is empty. You must provide the file path. "
            "Example: read_file(path='C:/chhelu 1/analysis/site3_report.html', offset=0)"
        )
    # Catch the common mistake of passing a line number as path
    if path.strip().lstrip("-").isdigit():
        return (
            f"Error: '{path}' looks like a line number, not a file path. "
            "The 'path' parameter must be the file path (e.g. 'C:/chhelu 1/analysis/site3_report.html'). "
            f"Use offset={path} to start reading from that line."
        )
    try:
        p = Path(path)
        if p.is_dir():
            return f"Error: '{path}' is a directory. Use list_dir to explore it."
        if not limit or limit <= 0:
            limit = _READ_DEFAULT_LIMIT
        if offset < 0:
            offset = 0
        lines = _read_text_safe(p).splitlines()
        total = len(lines)
        chunk = lines[offset:offset + limit]
        end   = min(offset + limit, total)
        header = f"File: {path} (lines {offset+1}-{end} of {total})\n"
        numbered = "\n".join(f"{offset+i+1}\t{line}" for i, line in enumerate(chunk))
        if end < total:
            numbered += (
                f"\n\n[file continues — {total - end} more lines. "
                f"Call read_file with offset={end} to keep reading, "
                f"or grep to jump straight to what you need.]"
            )
        return header + numbered
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as e:
        return f"Error: {e}"

def write_file(path: str, content: str, mode: str = "w") -> str:
    if not path or not path.strip():
        return (
            "Error: 'path' is empty. Provide the full file path. "
            "For large files, write in chunks: "
            "first write_file(path='C:/chhelu 1/analysis/master_dashboard.html', content='<html>...', mode='w'), "
            "then write_file(path='C:/chhelu 1/analysis/master_dashboard.html', content='...more...', mode='a')"
        )
    if path.strip() in (".", "./", "/", "\\", ".."):
        return f"Error: '{path}' is not a file path. Use a full path like 'C:/chhelu 1/analysis/master_dashboard.html'."
    try:
        p = Path(path)
        if p.exists() and p.is_dir():
            return f"Error: '{path}' is a directory. Include a filename."
        already_existed = p.exists() and p.is_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        write_mode = "a" if mode == "a" else "w"
        with open(p, write_mode, encoding="utf-8") as f:
            f.write(content)
        total_lines = len(p.read_text(encoding="utf-8").splitlines())
        if write_mode == "a":
            return f"Appended to {path} ({total_lines} lines total)"
        warning = "  WARNING: File already existed and was overwritten. If you had not read it first, use git diff to check for lost content." if already_existed else ""
        return f"Written {path} ({total_lines} lines total){warning}"
    except Exception as e:
        return f"Error writing '{path}': {e}"

def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    if not path or not path.strip():
        return (
            "Error: 'path' is empty. You must provide the file path to edit. "
            "Example: edit_file(path='C:/chhelu 1/analysis/site3_report.html', old_string='...', new_string='...')"
        )
    if path.strip() in (".", "./", "/", "\\", ".."):
        return f"Error: '{path}' is not a valid file path. Provide the full path including filename."
    try:
        p = Path(path)
        if p.is_dir():
            return f"Error: '{path}' is a directory, not a file."

        content = _read_text_safe(p)

        # Normalize CRLF → LF on both sides before matching (Windows files vs typed strings)
        content_n = content.replace("\r\n", "\n")
        old_n     = old_string.replace("\r\n", "\n")

        if old_n in content_n:
            count = content_n.count(old_n)
            if replace_all:
                new_content = content_n.replace(old_n, new_string)
                p.write_text(new_content, encoding="utf-8")
                return f"Edited {path} ({count} occurrence{'s' if count != 1 else ''} replaced)"
            if count > 1:
                return (
                    f"Error: old_string appears {count} times in {path} — make it more unique, "
                    f"or use replace_all=true to replace all {count} occurrences at once."
                )
            new_content = content_n.replace(old_n, new_string, 1)
            p.write_text(new_content, encoding="utf-8")
            return f"Edited {path}"

        # Not found — give the model useful context so it can fix its old_string
        lines = content_n.splitlines()
        first_line = old_n.split("\n")[0].strip()

        # Find lines in the file that contain the first line of old_string
        hits = [i for i, l in enumerate(lines) if first_line and first_line in l]

        if hits:
            i = hits[0]
            old_line_count = len(old_n.split("\n"))
            start = max(0, i - 1)
            end   = min(len(lines), i + old_line_count + 2)
            snippet = "\n".join(f"  {start+j+1}: {lines[start+j]}" for j in range(end - start))
            hint = (
                f"\n\nNearest match in file (lines {start+1}-{end}):\n{snippet}"
                f"\n\nAction: Call read_file on this file, copy the exact text from those lines into old_string."
            )
        else:
            preview = "\n".join(f"  {i+1}: {l}" for i, l in enumerate(lines[:10]))
            hint = (
                f"\n\nFirst 10 lines of file:\n{preview}"
                f"\n\nAction: Call read_file on '{path}' and copy the exact text you want to replace."
            )

        return f"Error: old_string not found in {path}.{hint}"

    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as e:
        return f"Error: {e}"

def glob(pattern: str, directory: str = ".") -> str:
    try:
        base = Path(directory)
        matches = []
        # Path.glob natively supports ** recursion — never mangle the pattern.
        for p in base.glob(pattern):
            if not any(skip in p.parts for skip in SKIP_DIRS):
                matches.append(str(p))
        matches.sort()
        return "\n".join(matches[:200]) or "No files found"
    except Exception as e:
        return f"Error: {e}"

def grep(pattern: str, path: str = ".", glob: str = None) -> str:
    try:
        cmd = ["grep", "-rn", "--color=never", pattern, path]
        if glob:
            cmd += ["--include", glob]
        for skip in SKIP_DIRS:
            cmd += ["--exclude-dir", skip]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                                errors="replace", timeout=15)
        return result.stdout[:6000] or "No matches found"
    except FileNotFoundError:
        try:
            return _python_grep(pattern, path, glob)
        except Exception as e:
            return f"Error: {e}"
    except Exception as e:
        return f"Error: {e}"

def _python_grep(pattern: str, path: str, glob: str = None) -> str:
    results = []
    base = Path(path)
    if not base.exists():
        return f"Error: path not found: {path}"
    files = base.rglob(glob or "*") if base.is_dir() else [base]
    try:
        regex = re.compile(pattern)
    except re.error:
        # Invalid regex from the model — fall back to a literal search
        regex = re.compile(re.escape(pattern))
    for f in files:
        if not f.is_file():
            continue
        if any(skip in f.parts for skip in SKIP_DIRS):
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if regex.search(line):
                    results.append(f"{f}:{i}: {line}")
                    if len(results) >= 100:
                        break
        except Exception:
            continue
    return "\n".join(results) or "No matches found"

def web_fetch(url: str, max_chars: int = 8000) -> str:
    try:
        import urllib.request
        import html
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "BharatCode/1.0 (AI coding agent)"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        # Strip HTML tags for cleaner text
        import re as _re
        # Remove scripts and styles
        raw = _re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=_re.DOTALL | _re.IGNORECASE)
        # Remove all tags
        text = _re.sub(r"<[^>]+>", " ", raw)
        # Collapse whitespace
        text = _re.sub(r"\s{3,}", "\n\n", text)
        text = html.unescape(text).strip()

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncated at {max_chars} chars ...]"
        return text or "(empty response)"
    except Exception as e:
        return f"Error fetching {url}: {e}"

def list_dir(path: str = ".", recursive: bool = False) -> str:
    try:
        root = Path(path)
        if not root.exists():
            return f"Path not found: {path}"

        lines = []
        if recursive:
            items = sorted(root.rglob("*"))
        else:
            items = sorted(root.iterdir())

        for item in items:
            if any(skip in item.parts for skip in SKIP_DIRS):
                continue
            try:
                if item.is_dir():
                    lines.append(f"  [DIR]  {item.relative_to(root) if recursive else item.name}/")
                else:
                    size = item.stat().st_size
                    size_str = f"{size:>8,} B" if size < 1024 else f"{size//1024:>6,} KB"
                    lines.append(f"  {size_str}  {item.relative_to(root) if recursive else item.name}")
            except Exception:
                continue
        return f"Directory: {path}\n" + "\n".join(lines[:300]) or "(empty)"
    except Exception as e:
        return f"Error: {e}"


# ── Dispatcher ────────────────────────────────────────────────────────────────

def execute_tool(name: str, args: dict) -> str:
    dispatch = {
        "bash":           bash,
        "read_file":      read_file,
        "write_file":     write_file,
        "edit_file":      edit_file,
        "glob":           glob,
        "grep":           grep,
        "web_fetch":      web_fetch,
        "list_dir":       list_dir,
        "process_output": process_output,
        "process_kill":   process_kill,
    }
    if name == "remember":
        from .memory import remember as _remember
        fn = _remember
    else:
        fn = dispatch.get(name)
    if not fn:
        return f"Unknown tool: {name}"
    try:
        return fn(**args)
    except TypeError as e:
        # Model passed wrong/unknown argument names — tell it instead of crashing
        return f"Error: invalid arguments for {name}: {e}. Check the tool's parameter names and retry."
    except Exception as e:
        return f"Error executing {name}: {type(e).__name__}: {e}"
