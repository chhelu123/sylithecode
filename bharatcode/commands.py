"""
Slash commands — like Claude Code's /clear, /compact, /review, /cost, /doctor, /git, /memory, /skill, /plan.
"""
import os
from .ui import console, show_info, show_success, show_warning, show_error
from .config import load_config, save_config


# ── Interactive dropdown helpers ───────────────────────────────────────────────

def _try_questionary_select(prompt: str, choices: list) -> str | None:
    """
    Show an interactive arrow-key dropdown using questionary.
    Each item in choices is (display_label, value).
    Returns the selected value, or None if cancelled / questionary not installed.
    """
    try:
        import questionary
        from questionary import Style

        q_style = Style([
            ("highlighted",  "fg:cyan bold"),
            ("pointer",      "fg:cyan bold"),
            ("selected",     "fg:green"),
            ("question",     "fg:yellow bold"),
            ("instruction",  "fg:gray italic"),
        ])

        q_choices = [
            questionary.Choice(title=label, value=val)
            for label, val in choices
        ]
        q_choices.append(questionary.Separator())
        q_choices.append(questionary.Choice(title="↩  Cancel", value=None))

        result = questionary.select(
            prompt,
            choices=q_choices,
            style=q_style,
            instruction=" (↑↓ move  Enter select  Ctrl-C cancel)",
        ).ask()
        return result
    except ImportError:
        return "__FALLBACK__"
    except (KeyboardInterrupt, EOFError):
        return None


def _numbered_select(title: str, choices: list) -> str | None:
    """
    Fallback numbered list when questionary is not installed.
    choices = [(label, value), ...]
    """
    console.print(f"\n[bold]{title}[/bold]")
    for i, (label, val) in enumerate(choices, 1):
        console.print(f"  [green]{i:>2}[/green]  {label}")
    console.print()
    try:
        raw = input("  Enter number (or name, or Enter to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(choices):
            return choices[idx][1]
        show_error(f"Invalid number: {raw}")
        return None
    # Try matching by value
    for label, val in choices:
        if raw.lower() == str(val).lower():
            return val
    show_error(f"Not found: {raw}")
    return None


def _select(prompt: str, choices: list) -> str | None:
    """Show questionary dropdown, fallback to numbered list."""
    result = _try_questionary_select(prompt, choices)
    if result == "__FALLBACK__":
        return _numbered_select(prompt, choices)
    return result

COMMANDS: dict[str, dict] = {}

def command(name: str, description: str):
    def decorator(fn):
        COMMANDS[name] = {"fn": fn, "description": description}
        return fn
    return decorator

def handle_slash_command(cmd: str, session: dict) -> bool:
    parts = cmd.strip().lstrip("/").split(maxsplit=1)
    name  = parts[0].lower()
    args  = parts[1] if len(parts) > 1 else ""

    if name in COMMANDS:
        COMMANDS[name]["fn"](args, session)
        return True

    show_warning(f"Unknown command: /{name}  — type /help")
    return False

# ── Conversation ──────────────────────────────────────────────────────────────

@command("clear", "Clear conversation history")
def cmd_clear(args: str, session: dict):
    session["messages"] = []
    show_success("Conversation cleared.")


@command("changes", "Show all files modified this session")
def cmd_changes(args: str, session: dict):
    change_log = session.get("change_log", {})
    if not change_log:
        show_info("No files modified this session.")
        return
    console.print(
        f"\n[bold]Session Changes[/bold]  "
        f"[dim]({len(change_log)} file{'s' if len(change_log) > 1 else ''} touched)[/dim]\n"
    )
    for path in sorted(change_log.keys()):
        stats  = change_log[path]
        writes = stats.get("writes", 0)
        edits  = stats.get("edits", 0)
        parts  = []
        if writes:
            parts.append(f"[green]{writes} write{'s' if writes > 1 else ''}[/green]")
        if edits:
            parts.append(f"[cyan]{edits} edit{'s' if edits > 1 else ''}[/cyan]")
        stat_str = "  ".join(parts) if parts else "[dim]touched[/dim]"
        console.print(f"  [cyan]{path}[/cyan]  {stat_str}")
    console.print()

@command("compact", "Summarise old conversation history to free up context (use when session is very long)")
def cmd_compact(args: str, session: dict):
    from .agent import _auto_compact, _estimate_tokens
    from openai import OpenAI
    from .config import get_api_key, load_config

    msgs = session.get("messages", [])
    if len(msgs) < 4:
        show_info("Not enough history to compact.")
        return

    before = _estimate_tokens(msgs)
    cfg    = load_config()
    client = OpenAI(api_key=get_api_key(), base_url="https://api.deepseek.com")

    # Force compact regardless of threshold
    from .agent import _COMPACT_TARGET_RATIO
    import math
    cutoff = max(2, math.floor(len(msgs) * _COMPACT_TARGET_RATIO))
    # Temporarily lower threshold so _auto_compact fires
    import bharatcode.agent as _ag
    old_thresh = _ag._COMPACT_THRESHOLD
    _ag._COMPACT_THRESHOLD = 0
    compacted = _auto_compact(
        msgs, client, cfg.get("model", "deepseek-v4-flash"),
        file_cache=session.get("file_cache", {}),    # Feature 6: pass cache for context-aware compact
    )
    _ag._COMPACT_THRESHOLD = old_thresh

    after = _estimate_tokens(msgs)
    if compacted:
        show_success(f"Compacted: ~{before:,} → ~{after:,} tokens ({len(msgs)} messages remaining)")
    else:
        show_info("Nothing to compact.")

# ── Code Actions ──────────────────────────────────────────────────────────────

@command("review", "Review current directory code")
def cmd_review(args: str, session: dict):
    from .agent import run_agent
    target = args or os.getcwd()
    show_info(f"Reviewing: {target}")
    run_agent(
        f"Do a thorough code review of: {target}. Check bugs, security, Indian compliance.",
        project_path=os.getcwd(),
    )

@command("audit", "Run Indian compliance audit (DPDP, RBI, GST)")
def cmd_audit(args: str, session: dict):
    from .agent import run_agent
    show_info("Running Indian compliance audit...")
    run_agent(
        "Audit this project for DPDP Act 2023, RBI, GST, Aadhaar/PAN compliance.",
        project_path=os.getcwd(),
    )

@command("plan", "Toggle plan mode — agent reads only and proposes plan before any changes")
def cmd_plan(args: str, session: dict):
    arg = args.strip().lower()

    # Explicit on/off/approve
    if arg in ("on",):
        session["plan_mode"] = True
    elif arg in ("off", "approve", "go", "execute", "yes", "y"):
        session["plan_mode"] = False
    else:
        session["plan_mode"] = not session.get("plan_mode", False)

    if session.get("plan_mode"):
        console.print(
            "\n[bold cyan]PLAN MODE ON[/bold cyan]  "
            "[dim]Agent will only read files and propose a plan — no writes, no bash.[/dim]\n"
            "[dim]When you're happy with the plan, type [/dim][cyan]/plan off[/cyan][dim] then re-send your task to execute.[/dim]\n"
        )
    else:
        console.print(
            "\n[bold green]PLAN MODE OFF[/bold green]  "
            "[dim]Agent can now write files and run commands.[/dim]\n"
        )

# ── New Website / App ────────────────────────────────────────────────────────

@command("newsite", "Build a website: /newsite Chhelu Portfolio - developer portfolio")
def cmd_newsite(args: str, session: dict):
    from .skills import ask_skill_questions, build_skill_prompt
    from .agent import run_agent
    import os

    prefilled: dict = {}
    if args.strip():
        parts = args.strip().split(" - ", 1) if " - " in args else args.strip().split(",", 1)
        if len(parts) == 2:
            prefilled["name"] = parts[0].strip()
            prefilled["desc"] = parts[1].strip()
        else:
            prefilled["name"] = args.strip()

    answers = ask_skill_questions("newsite", prefilled=prefilled)
    if answers is None:
        return

    name = answers.get("name", "website")
    if not name:
        show_error("Site name is required.")
        return

    dest      = name.lower().replace(" ", "-")
    dest_path = os.path.join(os.getcwd(), dest)
    os.makedirs(dest_path, exist_ok=True)
    console.print(f"[dim]Output: {dest_path}[/dim]\n")

    task = f"""{build_skill_prompt("newsite", answers)}

Output folder (absolute): {dest_path}

CRITICAL PATH RULE: Every file MUST use the full absolute path.
  Correct: <<<FILE:{dest_path}/frontend/src/App.jsx>>>
  Correct: <<<FILE:{dest_path}/backend/app/__init__.py>>>
  WRONG:   <<<FILE:frontend/src/App.jsx>>>"""

    run_agent(task, project_path=dest_path,
              auto_approve=session.get("auto_approve", False),
              history=session.get("messages"),
              system_content=session.get("system"),
              file_cache=session.get("file_cache"))


@command("newapp", "Build an app: /newapp TaskFlow - kanban project manager")
def cmd_newapp(args: str, session: dict):
    from .skills import ask_skill_questions, build_skill_prompt
    from .agent import run_agent
    import os

    prefilled: dict = {}
    raw = args.strip()
    if raw:
        parts = raw.split(" - ", 1) if " - " in raw else raw.split(",", 1)
        if len(parts) == 2:
            prefilled["name"] = parts[0].strip()
            prefilled["desc"] = parts[1].strip()
        else:
            prefilled["name"] = raw.strip()

    answers = ask_skill_questions("newapp", prefilled=prefilled)
    if answers is None:
        return

    name = answers.get("name", "app")
    if not name:
        show_error("App name is required.")
        return

    dest      = name.lower().replace(" ", "-")
    dest_path = os.path.join(os.getcwd(), dest)
    os.makedirs(dest_path, exist_ok=True)
    console.print(f"[dim]Output: {dest_path}[/dim]\n")

    task = f"""{build_skill_prompt("newapp", answers)}

Output folder (absolute): {dest_path}

CRITICAL PATH RULE: Every file MUST use the full absolute path.
  Correct: <<<FILE:{dest_path}/frontend/src/App.jsx>>>
  Correct: <<<FILE:{dest_path}/backend/app/__init__.py>>>
  WRONG:   <<<FILE:frontend/src/App.jsx>>>"""

    run_agent(task, project_path=dest_path,
              auto_approve=session.get("auto_approve", False),
              history=session.get("messages"),
              system_content=session.get("system"),
              file_cache=session.get("file_cache"))


# ── Skills ────────────────────────────────────────────────────────────────────

_SKILL_DESCRIPTIONS = {
    "newsite":   "Full-stack site — pick frontend + backend tech, frontend/ + backend/ folders",
    "newapp":    "Full-stack app  — pick frontend + backend tech, detailed per-framework rules",
    "docker":    "Dockerize everything — multi-stage build, compose, healthcheck, .dockerignore",
    "ci-github": "GitHub Actions CI/CD — lint → test → build → deploy, caching, secrets",
}


@command("skills", "Browse and run skills interactively (arrow-key dropdown)")
def cmd_skills(args: str, session: dict):
    from .skills import load_skills, BUILTIN_SKILLS, ask_skill_questions, build_skill_prompt, get_skill_raw
    from .agent import run_agent

    skills  = load_skills()
    builtin = list(BUILTIN_SKILLS.keys())
    custom  = [k for k in skills if k not in builtin]

    choices = []
    for name in builtin:
        desc = _SKILL_DESCRIPTIONS.get(name, "")
        choices.append((f"{name:<18} [dim]{desc}[/dim]", name))
    for name in custom:
        preview = skills[name].split("\n")[0][:55]
        choices.append((f"{name:<18} [cyan](custom)[/cyan] {preview}", name))

    selected = _select("Select a skill to run:", choices)
    if not selected:
        return

    _run_skill(selected, session)


@command("skill", "Run a skill directly: /skill razorpay")
def cmd_skill(args: str, session: dict):
    if not args:
        cmd_skills("", session)
        return
    _run_skill(args.strip().lower(), session)


def _run_skill(name: str, session: dict) -> None:
    """Ask Q&A for a skill, build the prompt, and run the agent."""
    from .skills import BUILTIN_SKILLS, ask_skill_questions, build_skill_prompt, get_skill_raw
    from .agent import run_agent
    import os

    if name in BUILTIN_SKILLS:
        # Interactive Q&A for built-in skills
        answers = ask_skill_questions(name)
        if answers is None:
            return  # user cancelled

        # Scaffold skills need a real output folder
        task_prompt = build_skill_prompt(name, answers)

        if name in ("newsite", "newapp"):
            proj_name = answers.get("name", name)
            dest      = proj_name.lower().replace(" ", "-")
            dest_path = os.path.join(os.getcwd(), dest)
            os.makedirs(dest_path, exist_ok=True)
            console.print(f"[dim]Output: {dest_path}[/dim]\n")
            task_prompt = (
                f"{task_prompt}\n\nOutput folder (absolute): {dest_path}\n"
                f"CRITICAL PATH RULE: Every file MUST be written inside {dest_path}."
            )
            run_agent(task_prompt, project_path=dest_path,
                      auto_approve=session.get("auto_approve", False),
                      history=session.get("messages"),
                      system_content=session.get("system"),
                      file_cache=session.get("file_cache"))
        else:
            show_info(f"Running skill: {name}")
            run_agent(task_prompt, project_path=os.getcwd(),
                      auto_approve=session.get("auto_approve", False),
                      history=session.get("messages"),
                      system_content=session.get("system"),
                      file_cache=session.get("file_cache"))
    else:
        # Custom file-based skill — raw prompt, no Q&A
        raw = get_skill_raw(name)
        if not raw:
            show_error(f"Skill '{name}' not found. Type /skills to browse.")
            return
        show_info(f"Running custom skill: {name}")
        run_agent(raw, project_path=os.getcwd(),
                  auto_approve=session.get("auto_approve", False),
                  history=session.get("messages"),
                  system_content=session.get("system"),
                  file_cache=session.get("file_cache"))

# ── Git ───────────────────────────────────────────────────────────────────────

@command("git", "Show git status and recent commits")
def cmd_git(args: str, session: dict):
    import subprocess
    try:
        status = subprocess.run(
            ["git", "status", "--short"], capture_output=True, text=True, timeout=5
        ).stdout
        log = subprocess.run(
            ["git", "log", "--oneline", "-8"], capture_output=True, text=True, timeout=5
        ).stdout
        branch = subprocess.run(
            ["git", "branch", "--show-current"], capture_output=True, text=True, timeout=5
        ).stdout.strip()

        console.print(f"\n[bold]Git Status[/bold]  branch: [cyan]{branch}[/cyan]")
        if status:
            for line in status.splitlines():
                color = "green" if line.startswith("?") else "yellow" if line.startswith("M") else "red"
                console.print(f"  [{color}]{line}[/{color}]")
        else:
            console.print("  [dim]Clean working tree[/dim]")

        if log:
            console.print("\n[bold]Recent Commits[/bold]")
            for line in log.splitlines():
                sha, *rest = line.split(" ", 1)
                console.print(f"  [dim]{sha}[/dim] {' '.join(rest)}")
        console.print()
    except FileNotFoundError:
        show_error("git not found in PATH")
    except Exception as e:
        show_error(str(e))

@command("diff", "Show uncommitted git changes")
def cmd_diff(args: str, session: dict):
    import subprocess
    try:
        diff = subprocess.run(
            ["git", "diff", "--stat"], capture_output=True, text=True, timeout=5
        ).stdout
        full = subprocess.run(
            ["git", "diff"], capture_output=True, text=True, timeout=5
        ).stdout
        if not diff and not full:
            console.print("[dim]No uncommitted changes.[/dim]")
            return
        console.print(f"\n[bold]Uncommitted Changes[/bold]\n{diff}")
        if full and len(full) < 6000:
            from rich.syntax import Syntax
            console.print(Syntax(full, "diff", theme="monokai"))
        elif full:
            console.print(f"[dim](diff too large to display — {len(full):,} chars)[/dim]")
    except FileNotFoundError:
        show_error("git not found")
    except Exception as e:
        show_error(str(e))

# ── Cost & Status ─────────────────────────────────────────────────────────────

@command("cost", "Show session token usage and estimated cost")
def cmd_cost(args: str, session: dict):
    from .cost import session_cost
    session_cost.display(console)

@command("status", "Show BharatCode version, model, API status")
def cmd_status(args: str, session: dict):
    import platform
    from . import __version__
    cfg = load_config()
    key = cfg.get("api_key", "")
    key_display = (key[:8] + "..." + key[-4:]) if key else "[red]NOT SET[/red]"

    # Test API connectivity
    api_ok = False
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
        client.models.list()
        api_ok = True
    except Exception:
        pass

    console.print(f"\n[bold]BharatCode Status[/bold]")
    console.print(f"  [dim]Version[/dim]    [cyan]{__version__}[/cyan]")
    console.print(f"  [dim]Model[/dim]      [cyan]{cfg.get('model', 'deepseek-v4-flash')}[/cyan]")
    console.print(f"  [dim]API key[/dim]    {key_display}")
    console.print(f"  [dim]API status[/dim] {'[green]connected[/green]' if api_ok else '[red]unreachable[/red]'}")
    console.print(f"  [dim]Python[/dim]     [cyan]{platform.python_version()}[/cyan]")
    console.print(f"  [dim]Platform[/dim]   [cyan]{platform.system()} {platform.release()}[/cyan]")
    console.print(f"  [dim]Workdir[/dim]    [cyan]{os.getcwd()}[/cyan]")
    console.print()

@command("doctor", "Diagnose BharatCode setup")
def cmd_doctor(args: str, session: dict):
    import subprocess
    checks = []

    # Python version
    import sys
    py_ok = sys.version_info >= (3, 10)
    checks.append(("Python >= 3.10", py_ok, f"Python {sys.version.split()[0]}"))

    # API key
    cfg = load_config()
    key = cfg.get("api_key", "")
    checks.append(("DeepSeek API key set", bool(key), key[:8] + "..." if key else "NOT SET"))

    # API connectivity
    api_ok = False
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
        client.models.list()
        api_ok = True
    except Exception as e:
        pass
    checks.append(("DeepSeek API reachable", api_ok, "OK" if api_ok else "Connection failed"))

    # Git
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=3)
        git_ok = r.returncode == 0
        git_ver = r.stdout.strip()
    except Exception:
        git_ok, git_ver = False, "not found"
    checks.append(("git installed", git_ok, git_ver))

    # Required packages
    for pkg in ["rich", "click", "openai", "dotenv"]:
        try:
            __import__(pkg.replace("-", "_"))
            checks.append((f"package: {pkg}", True, "installed"))
        except ImportError:
            checks.append((f"package: {pkg}", False, "MISSING — run: pip install " + pkg))

    # BHARATCODE.md
    has_md = (os.path.exists("BHARATCODE.md"))
    checks.append(("BHARATCODE.md in project", has_md, "found" if has_md else "run: bharatcode init"))

    console.print("\n[bold]BharatCode Doctor[/bold]\n")
    for name, ok, detail in checks:
        icon  = "[green]✓[/green]" if ok else "[red]✗[/red]"
        color = "dim" if ok else "red"
        console.print(f"  {icon}  {name:<30} [{color}]{detail}[/{color}]")
    console.print()
    all_ok = all(ok for _, ok, _ in checks)
    if all_ok:
        show_success("All checks passed! BharatCode is ready.")
    else:
        show_warning("Some checks failed. Fix the issues above.")

# ── Memory ────────────────────────────────────────────────────────────────────

@command("memory", "Manage persistent memory: /memory [list] | /memory add <text> | /memory del <id>")
def cmd_memory(args: str, session: dict):
    from .memory import add_memory, delete_memory, show_memories, MEMORY_DIR
    parts = args.strip().split(maxsplit=1)
    sub   = parts[0].lower() if parts else "list"
    rest  = parts[1] if len(parts) > 1 else ""

    if sub in ("list", "ls", ""):
        show_memories(console)
        console.print(f"[dim]  Memory dir: {MEMORY_DIR}[/dim]")
    elif sub == "add":
        if not rest:
            show_error("Usage: /memory add <text>")
            return
        entry = add_memory(rest)
        show_success(f"Memory saved (id={entry['id']}): {rest[:60]}")
    elif sub in ("del", "delete", "rm"):
        try:
            mid = int(rest)
            if delete_memory(mid):
                show_success(f"Memory {mid} deleted.")
            else:
                show_error(f"Memory id={mid} not found.")
        except ValueError:
            show_error("Usage: /memory del <id>")
    else:
        # Bare text — treat whole args as "add"
        entry = add_memory(args)
        show_success(f"Memory saved (id={entry['id']}): {args[:60]}")


@command("resume", "Resume a previous session: /resume [session_id]")
def cmd_resume(args: str, session: dict):
    from . import session_storage
    import os

    cwd     = os.getcwd()
    args    = args.strip()
    recent  = session_storage.list_recent(cwd, max_n=5)

    if not recent:
        show_info("No previous sessions found for this directory.")
        return

    # Pick which session to load
    if args:
        # User specified a session ID prefix
        match = next((s for s in recent if s["session_id"].startswith(args)), None)
        if not match:
            show_error(f"Session '{args}' not found.")
            for s in recent:
                console.print(f"  [dim]{s['session_id']}[/dim]  {s['mtime_str']}  {s['last_message']}")
            return
        chosen = match
    else:
        # Show list and let user pick
        choices = [
            (
                f"{s['session_id']}  {s['mtime_str']}  ({s['turns']} turns)  \"{s['last_message']}\"",
                s["session_id"],
            )
            for s in recent
        ]
        picked = _select("Resume which session?", choices)
        if not picked:
            return
        chosen = next(s for s in recent if s["session_id"] == picked)

    # Load messages
    messages = session_storage.load_messages(chosen["path"])
    if not messages:
        show_error("Session file is empty.")
        return

    # Restore into session
    session["messages"][:] = messages
    session_storage.save_latest_pointer(cwd, chosen["session_id"])

    show_success(
        f"Resumed session {chosen['session_id']} — "
        f"{len(messages)} messages, {chosen['turns']} user turns."
    )
    console.print(
        f"[dim]  Last message: \"{chosen['last_message']}\"[/dim]\n"
        f"[dim]  Continue where you left off — or just start typing a new task.[/dim]"
    )

# ── Settings ──────────────────────────────────────────────────────────────────

@command("yolo", "Toggle auto-approve mode (skip all permission prompts)")
def cmd_yolo(args: str, session: dict):
    session["auto_approve"] = not session.get("auto_approve", False)
    if session["auto_approve"]:
        console.print("[yellow]Auto-approve ON[/yellow] — all bash commands run without prompts.")
    else:
        console.print("[green]Auto-approve OFF[/green] — permission prompts restored.")

@command("model", "Switch model: /model deepseek-v4-flash | deepseek-v4-pro")
def cmd_model(args: str, session: dict):
    if not args:
        cfg = load_config()
        show_info(f"Current model: {cfg.get('model')}")
        console.print("  Options: [cyan]deepseek-v4-flash[/cyan]  [cyan]deepseek-v4-pro[/cyan]")
        console.print("  [dim]Aliases:  deepseek-chat → v4-flash  |  deepseek-reasoner → v4-pro[/dim]")
        return
    cfg = load_config()
    cfg["model"] = args.strip()
    save_config(cfg)
    show_success(f"Model switched to: {args.strip()}")

@command("pwd", "Show current working directory")
def cmd_pwd(args: str, session: dict):
    show_info(os.getcwd())

@command("config", "Show current config")
def cmd_config(args: str, session: dict):
    cfg = load_config()
    console.print("\n[bold]Config[/bold]")
    for k, v in cfg.items():
        if k == "api_key" and v:
            v = v[:8] + "..." + v[-4:]
        console.print(f"  [dim]{k:<20}[/dim] [cyan]{v}[/cyan]")
    console.print()

# ── Agents ────────────────────────────────────────────────────────────────────

@command("agent", "Spawn a specialist AI agent — explore, coder, verifier, researcher: /agent <type> <task>")
def cmd_agent(args: str, session: dict):
    from .subagent import run_subagent, AGENT_TYPES

    parts     = args.strip().split(maxsplit=1)
    agent_type = parts[0].lower() if parts else ""
    task       = parts[1] if len(parts) > 1 else ""

    if not agent_type or agent_type not in AGENT_TYPES:
        # Show interactive selector with taglines
        choices = [
            (
                f"{info['icon']}  [{atype:<10}]  {info['tagline']}",
                atype,
            )
            for atype, info in AGENT_TYPES.items()
        ]
        console.print(
            "\n[bold]BharatCode Agents[/bold]  [dim]— each specialist runs with its own isolated context[/dim]"
        )
        agent_type = _select("Which agent do you want to spawn?", choices)
        if not agent_type:
            return

    if not task:
        try:
            info = AGENT_TYPES[agent_type]
            console.print(
                f"\n  {info['icon']} [bold {info['color']}]{info['label']}[/bold {info['color']}]  "
                f"[dim]{info['tagline']}[/dim]\n"
            )
            console.print("[dim]What should this agent do? Be specific — it has no memory of your session.[/dim]")
            task = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            pass
    if not task:
        show_error("No task provided.")
        return

    result = run_subagent(
        task=task,
        agent_type=agent_type,
        project_path=os.getcwd(),
        parent_system=session.get("system"),
        parent_file_cache=session.get("file_cache"),
    )
    if result.success:
        if result.output:
            from rich.panel import Panel
            from rich.markdown import Markdown
            console.print(Panel(
                Markdown(result.output),
                border_style="cyan",
                title=f"[bold cyan]{AGENT_TYPES[agent_type]['label']} Agent[/bold cyan]",
                padding=(0, 1),
            ))
        show_success(f"{AGENT_TYPES[agent_type]['label']} agent completed in {result.duration:.1f}s")
    else:
        show_error(f"Agent failed: {result.error}")


# ── Coordinator Mode ──────────────────────────────────────────────────────────

@command("coordinator", "Enter coordinator mode — main agent orchestrates parallel specialist workers")
def cmd_coordinator(args: str, session: dict):
    from .coordinator import WorkerPool, COORDINATOR_SYSTEM_PROMPT

    if session.get("coordinator_mode"):
        # Already in coordinator mode — show live worker status
        pool = session.get("worker_pool")
        if pool:
            console.print("\n[bold cyan]Coordinator — Active Workers[/bold cyan]")
            console.print(pool.status_table())
        return

    from rich.panel import Panel
    console.print(Panel(
        "[bold cyan]Coordinator Mode[/bold cyan]\n\n"
        "BharatCode is now your orchestrator. It will:\n\n"
        "  🚀  [cyan]spawn_worker[/cyan]   — launch parallel specialist agents (non-blocking)\n"
        "  📨  [cyan]send_message[/cyan]   — continue a worker with new instructions\n"
        "  🛑  [cyan]task_stop[/cyan]      — kill a worker that went off track\n\n"
        "Workers run in background threads and report back via [dim]<task-notification>[/dim] messages.\n"
        "The coordinator synthesizes their findings and directs the next phase.\n\n"
        "[dim]Worker types: explore / coder / verifier / researcher / general[/dim]\n"
        "[dim]Type /workers to see active workers. /exit-coordinator to return to normal.[/dim]",
        border_style="cyan",
        title="[bold cyan]⚡ Coordinator Mode[/bold cyan]",
        padding=(1, 2),
    ))

    pool = WorkerPool()
    base_system  = session.get("system", "")

    # Save base system so we can restore it on exit
    session["base_system"]      = base_system
    session["system"]           = base_system + COORDINATOR_SYSTEM_PROMPT
    session["coordinator_mode"] = True
    session["worker_pool"]      = pool

    show_success("Coordinator mode active. Send your task and I'll orchestrate workers to solve it.")


@command("workers", "Show status of all coordinator workers in this session")
def cmd_workers(args: str, session: dict):
    if not session.get("coordinator_mode"):
        show_warning("Not in coordinator mode. Type /coordinator to enter.")
        return
    pool = session.get("worker_pool")
    if not pool:
        show_warning("No worker pool found.")
        return
    console.print("\n[bold]Active Workers[/bold]")
    console.print(pool.status_table())


@command("exit-coordinator", "Exit coordinator mode and return to normal agent mode")
def cmd_exit_coordinator(args: str, session: dict):
    if not session.get("coordinator_mode"):
        show_info("Already in normal mode.")
        return

    pool = session.get("worker_pool")
    if pool:
        # Show final summary before exit
        from rich.table import Table
        console.print("\n[bold]Final Worker Summary[/bold]")
        console.print(pool.status_table())

    session["coordinator_mode"] = False
    session["worker_pool"]      = None
    session["system"]           = session.get("base_system", session.get("system", ""))
    show_success("Returned to normal agent mode.")


# ── Help ──────────────────────────────────────────────────────────────────────

_HELP_GROUPS = {
    "Scaffold":      ["newsite", "newapp"],
    "Coordinator":   ["coordinator", "workers", "exit-coordinator"],
    "Sub-agents":    ["agent"],
    "Conversation":  ["clear", "compact", "changes", "resume"],
    "Code Actions":  ["review", "audit", "plan"],
    "Skills":        ["skills", "skill"],
    "Git":           ["git", "diff"],
    "Status":        ["cost", "status", "doctor"],
    "Memory":        ["memory"],
    "Settings":      ["yolo", "model", "config", "pwd"],
}

_CLI_CMDS = [
    ("bharatcode new website \"Name\"",              "Build a website from scratch"),
    ("bharatcode new website \"Name\" \"desc\"",     "Website with description"),
    ("bharatcode new app \"Name\" --type flask",     "Flask app from scratch"),
    ("bharatcode new app \"Name\" --type react",     "React app from scratch"),
    ("bharatcode new app \"Name\" --type fullstack", "Full-stack app from scratch"),
    ("bharatcode new app \"Name\" --type node",      "Node.js app from scratch"),
    ("bharatcode new app \"Name\" --type nextjs",    "Next.js app from scratch"),
    ("bharatcode fix \"bug\"",                       "Fix a bug"),
    ("bharatcode build \"feature\"",                 "Build a feature"),
    ("bharatcode review [path]",                     "Code review"),
    ("bharatcode test src/file.py",                  "Write & run tests"),
    ("bharatcode audit",                             "Indian compliance"),
    ("bharatcode ask \"question\"",                  "Ask about code"),
    ("bharatcode explain src/file.py",               "Explain a file"),
    ("bharatcode refactor src/file.py",              "Refactor code"),
    ("bharatcode init",                              "Create BHARATCODE.md"),
    ("bharatcode -y fix \"bug\"",                    "Fix, skip prompts"),
]


@command("help", "Show all commands (interactive dropdown)")
def cmd_help(args: str, session: dict):
    # Build flat choice list from all groups
    choices = []
    for group, names in _HELP_GROUPS.items():
        for name in names:
            if name in COMMANDS:
                desc = COMMANDS[name]["description"]
                choices.append((
                    f"/{name:<14} [dim]{desc}[/dim]  [bright_black]({group})[/bright_black]",
                    name,
                ))

    selected = _select("Select a command to learn more or run:", choices)

    if selected is None:
        # User cancelled — print full help instead
        _print_full_help()
        return

    # Show details for selected command
    desc = COMMANDS[selected]["description"]
    console.print(f"\n[bold green]/{selected}[/bold green]  {desc}\n")

    # Command-specific help text
    detailed = {
        "newsite": (
            "Builds a fully custom website — not a generic template, a real site designed for your project.\n"
            "Splits CSS into variables/reset/typography/layout/components/responsive files.\n"
            "Usage:   /newsite <name>\n"
            "         /newsite <name> - <description>\n"
            "Example: /newsite Chhelu Portfolio - dark theme developer portfolio with projects and blog"
        ),
        "newapp": (
            "Builds a complete application with proper file separation, real models, real routes, real logic.\n"
            "Full-stack apps get CORS configured, Vite proxy, .env files, and exact startup commands.\n"
            "Usage:   /newapp <name> [--flask|--react|--fullstack|--node|--nextjs]\n"
            "Example: /newapp ShopIndia e-commerce with Razorpay payments --fullstack"
        ),
        "agent": (
            "Spawn a specialist AI agent that runs with its own isolated context and dedicated tools.\n"
            "Types:\n"
            "  explore    — reads everything, writes nothing, maps your codebase\n"
            "  coder      — full access, ships complete production code\n"
            "  verifier   — ruthless auditor, finds every bug and security hole\n"
            "  researcher — live web fetcher, gets real docs and real examples\n"
            "  general    — all tools, no restrictions\n"
            "Usage:   /agent <type> <task>\n"
            "Example: /agent verifier Read backend/app.py and report every security issue with line numbers"
        ),
        "skills": (
            "Opens an interactive dropdown with all available skills.\n"
            "Arrow keys to navigate, Enter to select, Ctrl-C to cancel.\n"
            "After selecting, some skills (newsite/newapp) ask for a project name."
        ),
        "skill": (
            "Run a skill directly without the dropdown.\n"
            "Usage:   /skill <name>\n"
            "Example: /skill razorpay  |  /skill docker  |  /skill jwt-auth"
        ),
        "plan": (
            "Plan mode: agent reads files and proposes a plan — no writes, no bash.\n"
            "Review the plan, then type /plan off to let the agent execute it.\n"
            "/plan on   — enable (read-only)\n"
            "/plan off  — disable (agent can now write and run commands)"
        ),
        "compact": (
            "Compresses old conversation history into a dense summary to free up context tokens.\n"
            "Use when: the agent seems to lose track, or the session has been running for a long time.\n"
            "BharatCode also auto-compacts when history exceeds ~50,000 tokens."
        ),
        "yolo": (
            "Toggles auto-approve mode — skips all permission prompts for bash, write, and edit.\n"
            "Use when you trust the agent and want it to run without interruption.\n"
            "Green = ON (no prompts).  Default = OFF (prompts on bash commands)."
        ),
        "model": (
            "Switch the DeepSeek model. BharatCode also auto-selects based on task complexity.\n"
            "/model deepseek-v4-flash  — fast, cheap, great for most tasks (~$0.27/1M in)\n"
            "/model deepseek-v4-pro    — deeper reasoning, use for debugging / architecture (~$0.55/1M in)\n"
            "Old names still work: deepseek-chat → v4-flash  |  deepseek-reasoner → v4-pro\n"
            "Auto-select upgrades flash → pro for complex tasks automatically."
        ),
        "coordinator": (
            "Enters coordinator mode — BharatCode becomes an orchestrator that spawns\n"
            "parallel specialist workers instead of doing the work itself.\n\n"
            "Workflow:\n"
            "  1. You send a task (e.g. 'fix the auth bug and add tests')\n"
            "  2. Coordinator spawns parallel explore workers to map the code\n"
            "  3. When workers finish, coordinator receives <task-notification> messages\n"
            "  4. Coordinator synthesizes findings → spawns coder workers with precise specs\n"
            "  5. After implementation → spawns a fresh verifier to prove it works\n\n"
            "Worker types:\n"
            "  explore    — maps codebase (read-only, fast, can run 5 in parallel)\n"
            "  researcher — fetches live docs, API specs, real examples from the web\n"
            "  coder      — implements code, runs tests, commits\n"
            "  verifier   — audits code, runs tests, finds security holes\n"
            "  general    — all tools, use when task spans multiple roles\n\n"
            "Type /workers to see all active workers and their status.\n"
            "Type /exit-coordinator to return to normal mode."
        ),
        "memory": (
            "Persistent memory survives across sessions — the agent reads it at the start of every task.\n"
            "/memory list         — see everything saved\n"
            "/memory add <text>   — save a fact (paths, decisions, preferences, metrics)\n"
            "/memory del <id>     — remove a specific memory by its ID"
        ),
    }
    if selected in detailed:
        console.print(f"[dim]{detailed[selected]}[/dim]\n")

    # Ask if they want to run it
    try:
        run_it = input(f"  Run /{selected} now? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        run_it = ""

    if run_it != "y":
        return

    # Commands that still need a CLI argument before running.
    # newsite / newapp are NOT here — their built-in Q&A collects everything.
    _arg_hints = {
        "skill":   ("Skill name", "Example: razorpay"),
        "review":  ("Path to review (Enter for current dir)", ""),
        "explain": ("File to explain", "Example: src/auth.py"),
        "refactor":("File to refactor", "Example: src/utils.py"),
        "test":    ("File to test", "Example: src/payment.py"),
    }

    if selected in _arg_hints:
        hint, example = _arg_hints[selected]
        if example:
            console.print(f"  [dim]{example}[/dim]")
        try:
            extra = input(f"  /{selected} {hint}: ").strip()
        except (EOFError, KeyboardInterrupt):
            extra = ""

        # Commands where blank is valid (review defaults to cwd)
        if not extra and selected not in ("review",):
            show_error(f"No name provided — run /{selected} <name> manually.")
            return

        handle_slash_command(f"/{selected} {extra}".strip(), session)
    else:
        handle_slash_command(f"/{selected}", session)


def _print_full_help():
    """Print the traditional full help listing."""
    console.print()
    for group, names in _HELP_GROUPS.items():
        console.print(f"[bold]{group}[/bold]")
        for name in names:
            if name in COMMANDS:
                console.print(f"  [green]/{name:<14}[/green] {COMMANDS[name]['description']}")
        console.print()

    console.print("[bold]CLI Commands[/bold]")
    for cmd, desc in _CLI_CMDS:
        console.print(f"  [cyan]{cmd:<44}[/cyan] [dim]{desc}[/dim]")
    console.print()
    console.print("[dim]Tip: type /help for interactive mode, or /skills to browse skills with dropdown.[/dim]\n")
