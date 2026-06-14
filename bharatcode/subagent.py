"""
BharatCode Subagent System — parallel specialist agents that work while you work.

Each agent type has a dedicated role, restricted toolset, and purpose-built system prompt.
The main agent spawns them via the `spawn_agent` tool; they run, report back, and disappear.

Why this beats generic agent loops:
  ✦ Tool-level enforcement  — Explorer literally cannot write files (tool not in its list)
  ✦ Role-specific prompts   — each agent is primed to excel at one thing
  ✦ Shared file cache       — subagents never re-read what the parent already read
  ✦ No recursion            — subagents don't get spawn_agent, so no runaway chains
  ✦ Parallel capable        — run_subagents_parallel() fires multiple agents in threads

Agent types:
  explore    — silent analyst, reads everything, writes nothing, reports findings
  coder      — dedicated implementer, full system access, ships complete code
  verifier   — ruthless auditor, finds bugs and security holes before users do
  researcher — live web researcher, fetches real docs and real examples
  general    — all tools, no restrictions, deploy for anything
"""

import time
import threading
from dataclasses import dataclass
from io import StringIO
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


AGENT_TYPES: dict[str, dict] = {
    "explore": {
        "label": "Explorer",
        "icon": "🔍",
        "color": "cyan",
        "tagline": "Reads everything, writes nothing — maps your codebase with precision",
        "description": (
            "Silently reads your entire codebase — every route, every model, every config. "
            "Zero writes. Pure intelligence. Use before you build anything."
        ),
        "allowed_tools": {"read_file", "glob", "grep", "list_dir", "web_fetch"},
        "system_suffix": (
            "\n\n## SUBAGENT ROLE: EXPLORER (READ-ONLY)\n"
            "You are a silent intelligence agent. Your only job is to read, understand, and report.\n"
            "Map the codebase with precision: file structure, key functions, API routes, data models, dependencies.\n"
            "FORBIDDEN: write_file, edit_file, bash — these tools do not exist in your context.\n"
            "ALLOWED: read_file, glob, grep, list_dir, web_fetch.\n"
            "Output format: structured report with headings, bullet points, exact file paths and line numbers.\n"
            "Be thorough. The main agent is counting on your findings to build correctly."
        ),
    },
    "coder": {
        "label": "Coder",
        "icon": "💻",
        "color": "green",
        "tagline": "Full system access — writes and ships complete production code, no stubs ever",
        "description": (
            "Dedicated implementer with full system access. Writes complete production code, "
            "runs commands to verify it works. No stubs. No TODOs. Ships the real thing."
        ),
        "allowed_tools": None,  # all standard tools, spawn_agent excluded
        "system_suffix": (
            "\n\n## SUBAGENT ROLE: CODER\n"
            "You are a specialist implementer. You have one job: build it completely and correctly.\n"
            "Standards:\n"
            "  - Every function has a real implementation — no stubs, no TODOs, no 'add your code here'\n"
            "  - Every file is complete — not a skeleton, not a template, the actual working thing\n"
            "  - After writing, run bash to verify it compiles/starts/passes basic checks\n"
            "  - Use <<<FILE:>>> markers for files over 100 lines (avoids JSON truncation)\n"
            "Shared-project coordination (other agents may build sibling parts):\n"
            "  - BEFORE writing code: read API_CONTRACT.md and BUILD_LOG.md in the project root\n"
            "    if they exist. Follow API_CONTRACT.md EXACTLY — same endpoint paths, same JSON\n"
            "    keys, same ports, same env var names. Never change the contract; report\n"
            "    mismatches in your final summary instead.\n"
            "  - AFTER changing files: APPEND a short summary to BUILD_LOG.md (write_file\n"
            "    mode='a', never overwrite): files you created + endpoints/exports/ports defined.\n"
            "End your work with: exactly what you built, which files changed, and how to test it."
        ),
    },
    "verifier": {
        "label": "Verifier",
        "icon": "🛡️",
        "color": "yellow",
        "tagline": "Ruthless auditor — catches every bug and security hole before users do",
        "description": (
            "Ruthless code auditor — hunts down bugs, security holes, and broken logic "
            "line by line. Your last line of defence before users find the problems."
        ),
        "allowed_tools": {"read_file", "glob", "grep", "list_dir", "bash",
                          "web_fetch", "process_output", "process_kill"},
        "system_suffix": (
            "\n\n## SUBAGENT ROLE: VERIFIER\n"
            "You are a ruthless code auditor. Read everything. Assume nothing is correct.\n"
            "VERIFY BY RUNNING, not just by reading: start servers with "
            "bash(run_in_background=true), check boot logs with process_output, hit the "
            "health endpoint with web_fetch, then process_kill what you started.\n"
            "Hunt for every category of problem:\n"
            "  BUGS       — off-by-one, null dereference, wrong logic, edge cases not handled\n"
            "  SECURITY   — SQL injection, XSS, missing auth checks, exposed secrets, no rate limiting\n"
            "  INCOMPLETE — stub functions, empty handlers, missing error handling, unvalidated input\n"
            "  BROKEN API — CORS misconfigured, wrong port, hardcoded URL, missing /api/ prefix\n"
            "  BAD SETUP  — missing .env values, wrong package names, incorrect import paths\n"
            "For every issue: file name + line number + what is wrong + exactly how to fix it.\n"
            "Do not be polite. Be precise. The developer needs to know every problem before shipping."
        ),
    },
    "researcher": {
        "label": "Researcher",
        "icon": "📡",
        "color": "magenta",
        "tagline": "Fetches live docs and real API examples from the web — never guess again",
        "description": (
            "Fetches live documentation, real API specs, and working code examples straight "
            "from the web. Never guess an API again — get the exact answer in seconds."
        ),
        "allowed_tools": {"web_fetch", "read_file"},
        "system_suffix": (
            "\n\n## SUBAGENT ROLE: RESEARCHER\n"
            "You are a live documentation agent. Your job is to find accurate, current information fast.\n"
            "For each topic deliver:\n"
            "  1. What it is and what problem it solves (2 sentences max)\n"
            "  2. Exact installation command (pip install / npm install / etc.)\n"
            "  3. Minimal working code example — real code, not pseudocode\n"
            "  4. The most common pitfalls and how to avoid them\n"
            "  5. Source URL (official docs preferred over Stack Overflow)\n"
            "Fetch the official documentation. Don't rely on training data for version-sensitive APIs."
        ),
    },
    "general": {
        "label": "Agent",
        "icon": "🤖",
        "color": "blue",
        "tagline": "Full-capability autonomous agent — every tool, no restrictions, any task",
        "description": (
            "Full-capability autonomous agent with every tool available. "
            "Deploy when the task spans multiple roles or doesn't fit a single specialist."
        ),
        "allowed_tools": None,
        "system_suffix": (
            "\n\n## SUBAGENT ROLE: GENERAL PURPOSE\n"
            "You are a full-capability agent. Use whatever tools the task needs.\n"
            "Complete the task thoroughly and report back with clear results."
        ),
    },
}


@dataclass
class AgentResult:
    agent_type: str
    label: str
    task: str
    output: str
    success: bool
    duration: float = 0.0
    error: Optional[str] = None


def run_subagent(
    task: str,
    agent_type: str = "general",
    project_path: str = ".",
    parent_system: str = None,
    parent_file_cache: dict = None,
    context_history: list = None,
) -> AgentResult:
    """
    Run a specialized subagent synchronously.
    Shows a clear header/footer around its output so the user can distinguish
    the subagent's work from the main agent's work.
    Shares parent's file_cache so files already read are never re-fetched.
    Returns the agent's final text response.
    """
    from .agent import run_agent, _build_system, console

    info    = AGENT_TYPES.get(agent_type, AGENT_TYPES["general"])
    label   = info["label"]
    icon    = info["icon"]
    color   = info["color"]
    allowed = info["allowed_tools"]

    base_system  = parent_system or _build_system(project_path)
    agent_system = base_system + info["system_suffix"]

    agent_history = list(context_history or [])

    task_preview = task[:72] + "..." if len(task) > 72 else task
    console.print()
    console.print(f"  ┌{'─'*64}┐")
    console.print(f"  │  {icon} [bold {color}]Subagent: {label}[/bold {color}]"
                  + " " * max(0, 53 - len(label)) + "│")
    console.print(f"  │  [dim]{task_preview:<62}[/dim]  │")
    console.print(f"  └{'─'*64}┘")
    console.print()

    t0     = time.time()
    output = ""
    error  = None

    try:
        output = run_agent(
            task=task,
            project_path=project_path,
            auto_approve=True,
            history=agent_history,
            system_content=agent_system,
            file_cache=parent_file_cache if parent_file_cache is not None else {},
            allowed_tools=allowed,
            silent=True,
        ) or ""
    except Exception as exc:
        error  = str(exc)
        output = f"[Subagent error: {error}]"
        console.print(f"  [red]Subagent {label} failed:[/red] {error}")

    duration = time.time() - t0
    ok_icon  = "✅" if error is None else "❌"

    console.print()
    console.print(f"  ┌{'─'*64}┐")
    console.print(f"  │  {ok_icon} [bold {color}]{label} done[/bold {color}]  "
                  f"[dim]{duration:.1f}s[/dim]"
                  + " " * max(0, 50 - len(label)) + "│")
    console.print(f"  └{'─'*64}┘")
    console.print()

    return AgentResult(
        agent_type=agent_type,
        label=label,
        task=task,
        output=output,
        success=error is None,
        duration=duration,
        error=error,
    )


def run_subagents_parallel(
    tasks: list[dict],
    project_path: str = ".",
    parent_system: str = None,
    parent_file_cache: dict = None,
) -> list[AgentResult]:
    """
    Run multiple subagents in parallel threads.
    Each agent gets its own StringIO console so output doesn't interleave.
    After all complete, each agent's output is printed sequentially.
    Returns results in the same order as input tasks.

    Usage:
        results = run_subagents_parallel([
            {"task": "Analyze the backend routes", "agent_type": "explore"},
            {"task": "Check frontend components",  "agent_type": "explore"},
        ])
    """
    from .agent import _build_system, run_agent, console

    n       = len(tasks)
    results: list[Optional[AgentResult]] = [None] * n
    buffers: list[StringIO]              = [StringIO() for _ in range(n)]
    threads: list[threading.Thread]      = []

    def worker(idx: int, spec: dict):
        agent_type  = spec.get("agent_type", "general")
        task        = spec.get("task", "")
        info        = AGENT_TYPES.get(agent_type, AGENT_TYPES["general"])
        allowed     = info["allowed_tools"]
        base_system = parent_system or _build_system(project_path)
        agent_sys   = base_system + info["system_suffix"]
        buf_console = Console(file=buffers[idx], width=100, highlight=False, markup=True)

        t0     = time.time()
        error  = None
        output = ""
        try:
            output = run_agent(
                task=task,
                project_path=project_path,
                auto_approve=True,
                history=[],
                system_content=agent_sys,
                file_cache=parent_file_cache if parent_file_cache is not None else {},
                allowed_tools=allowed,
                silent=True,
            ) or ""
        except Exception as exc:
            error  = str(exc)
            output = f"[Error: {error}]"

        results[idx] = AgentResult(
            agent_type=agent_type,
            label=info["label"],
            task=task,
            output=output,
            success=error is None,
            duration=time.time() - t0,
            error=error,
        )

    # Print launch banner
    console.print(f"\n  [bold]Launching {n} parallel agents[/bold]")
    for i, spec in enumerate(tasks):
        info = AGENT_TYPES.get(spec.get("agent_type", "general"), AGENT_TYPES["general"])
        console.print(
            f"  [{i+1}] {info['icon']} [{info['color']}]{info['label']}[/{info['color']}]  "
            f"[dim]{spec.get('task', '')[:60]}[/dim]"
        )
    console.print()

    for i, spec in enumerate(tasks):
        t = threading.Thread(target=worker, args=(i, spec), daemon=True)
        threads.append(t)
        t.start()

    # Live status while waiting
    start = time.time()
    while any(t.is_alive() for t in threads):
        done  = sum(1 for r in results if r is not None)
        elapsed = time.time() - start
        time.sleep(0.5)

    for t in threads:
        t.join()

    # Display each agent's buffered output
    for i, result in enumerate(results):
        if result is None:
            continue
        info  = AGENT_TYPES.get(result.agent_type, AGENT_TYPES["general"])
        color = info["color"]
        icon  = info["icon"]
        ok    = "✅" if result.success else "❌"

        console.print(f"\n  {'═'*64}")
        console.print(
            f"  {ok} {icon} [bold {color}]{result.label}[/bold {color}] "
            f"[dim]completed in {result.duration:.1f}s[/dim]"
        )
        console.print(f"  {'═'*64}\n")

        # Print the buffered output
        buf_text = buffers[i].getvalue()
        if buf_text.strip():
            console.print(buf_text)

    console.print(f"\n  [bold]All {n} agents done.[/bold]  "
                  f"[dim]Total: {time.time() - start:.1f}s[/dim]\n")

    return [r for r in results if r is not None]


def format_results_for_model(results: list[AgentResult]) -> str:
    """Format multiple agent results as a string for the main model's context."""
    parts = []
    for r in results:
        parts.append(
            f"=== {r.label} ({r.agent_type}) — {r.duration:.1f}s ===\n"
            f"Task: {r.task}\n\n"
            f"{r.output}\n"
        )
    return "\n".join(parts)
