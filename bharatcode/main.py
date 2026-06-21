"""
Sylithe Code CLI — main entry point.
Inspired by Claude Code's CLI architecture.
"""
import os
import sys

# Force UTF-8 on Windows before anything else loads.
# Without this, emoji and Devanagari in source files crash the diff printer
# with UnicodeEncodeError: 'charmap' codec can't encode character.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass  # Python < 3.7 — best-effort

try:
    import readline  # noqa: F401 — enables arrow key history on Unix/Mac
except ImportError:
    readline = None  # not available on Windows — use pyreadline3 if needed
from pathlib import Path

import click
from rich.prompt import Prompt
from rich.rule import Rule

from .ui import (
    console, show_banner, show_error, show_success,
    show_info, show_warning, show_separator,
)
from .agent import run_agent, _build_system
from .config import load_config, save_config, get_api_key
from .commands import handle_slash_command

# ── Main Group ────────────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.pass_context
@click.argument("task", required=False, default=None)
@click.option("--auto-approve", "-y", is_flag=True, help="Auto-approve all bash commands (yolo mode)")
@click.option("--model", "-m", default=None, help="Override model for this run")
@click.option("--print", "-p", "print_mode", is_flag=True,
              help="Non-interactive: run task once and print output, then exit. "
                   "Reads from stdin if piped: cat file.py | bharatcode -p 'explain this'")
def cli(ctx, task, auto_approve, model, print_mode):
    """Sylithe Code — AI Coding Agent for Indian Developers

    \b
    Run without arguments to enter interactive mode.
    Pass a TASK with --print for non-interactive / piped use:
      bharatcode --print "explain this file" < src/app.py
      cat error.log | bharatcode -p "fix this"
    Or use a subcommand: fix, build, review, audit, test, explain...
    """
    ctx.ensure_object(dict)
    ctx.obj["auto_approve"] = auto_approve
    ctx.obj["model"]        = model

    if ctx.invoked_subcommand is not None:
        return

    # ── Non-interactive / pipe mode ───────────────────────────────────────────
    # Triggered by --print flag OR when stdin is a pipe/file (not a terminal).
    stdin_is_pipe = not sys.stdin.isatty()
    if print_mode or stdin_is_pipe:
        _print_mode(task=task, auto_approve=auto_approve, model=model)
        return

    # ── Interactive REPL ──────────────────────────────────────────────────────
    interactive_mode(auto_approve=auto_approve)

# ── Config ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--key",   help="Set DeepSeek API key")
@click.option("--show",  is_flag=True, help="Show current config")
@click.option("--model", help="Set model (deepseek-v4-flash or deepseek-v4-pro)")
@click.option("--auto-approve", is_flag=True, default=None, help="Enable auto-approve by default")
def config(key, show, model, auto_approve):
    """Configure Sylithe Code settings."""
    cfg = load_config()
    changed = False

    if key:
        cfg["api_key"] = key
        changed = True
        show_success("API key saved.")

    if model:
        cfg["model"] = model
        changed = True
        show_success(f"Model set to {model}.")

    if auto_approve is not None:
        cfg["auto_approve"] = auto_approve
        changed = True
        show_success(f"Auto-approve {'enabled' if auto_approve else 'disabled'}.")

    if changed:
        save_config(cfg)

    if show or not changed:
        console.print("\n[bold]Config[/bold]  [dim]~/.bharatcode/config.json[/dim]\n")
        for k, v in cfg.items():
            if k == "api_key" and v:
                v = v[:8] + "..." + v[-4:]
            console.print(f"  [dim]{k}[/dim]  [cyan]{v}[/cyan]")
        console.print()

# ── Fix ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("description")
@click.option("--file", "-f", help="File where bug is")
@click.option("--auto-approve", "-y", is_flag=True)
def fix(description, file, auto_approve):
    """Autonomously fix a bug.

    \b
    Examples:
      bharatcode fix "login fails with uppercase email"
      bharatcode fix "payment 500 error" -f src/payment.py
    """
    show_banner()
    console.print(f"\n[bold red]Bug:[/bold red] {description}\n")
    task = f"""Fix this bug: {description}
{"Focus on file: " + file if file else ""}

Steps:
1. Search for relevant code files
2. Read all related files carefully
3. Find the root cause
4. Fix with minimal changes
5. Run tests if they exist
6. Give a summary of what you fixed and why"""
    run_agent(task, os.getcwd(), auto_approve=auto_approve)

# ── Build ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("feature")
@click.option("--auto-approve", "-y", is_flag=True)
def build(feature):
    """Autonomously build a new feature.

    \b
    Examples:
      bharatcode build "add Razorpay payment integration"
      bharatcode build "add JWT authentication with refresh tokens"
    """
    show_banner()
    console.print(f"\n[bold green]Building:[/bold green] {feature}\n")
    task = f"""Build this feature: {feature}

Steps:
1. Read project structure (package.json / requirements.txt / pom.xml / build.gradle)
2. Understand existing code patterns and architecture
3. Plan the implementation
4. Implement following existing code style
5. Write basic tests
6. Run tests and fix failures
7. Summarize everything created and changed"""
    run_agent(task, os.getcwd())

# ── Review ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("target", default=".")
def review(target):
    """Review code for bugs, security, best practices.

    \b
    Examples:
      bharatcode review
      bharatcode review src/auth.py
      bharatcode review src/
    """
    show_banner()
    console.print(f"\n[bold blue]Reviewing:[/bold blue] {target}\n")
    task = f"""Do a thorough code review of: {target}

Check for:
1. Bugs and logic errors
2. Security vulnerabilities (SQL injection, XSS, auth bypass, insecure deserialization)
3. Indian regulatory issues (DPDP Act, RBI, GST logic errors) if applicable
4. Performance bottlenecks
5. Code quality (naming, duplication, complexity)

Output a structured report:
## Critical Issues
## Warnings
## Suggestions
## Positives"""
    run_agent(task, os.getcwd())

# ── Test ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("file")
@click.option("--auto-approve", "-y", is_flag=True)
def test(file, auto_approve):
    """Write and run tests for a file.

    \b
    Example:
      bharatcode test src/auth.py
      bharatcode test src/payment/razorpay.py
    """
    show_banner()
    console.print(f"\n[bold yellow]Writing tests for:[/bold yellow] {file}\n")
    task = f"""Write comprehensive tests for: {file}

Steps:
1. Read {file} fully
2. Find existing test files to match the testing framework and patterns
3. Write tests covering: happy path, edge cases, error cases, boundary values
4. Save to the appropriate test file
5. Run the tests
6. Fix any failures"""
    run_agent(task, os.getcwd(), auto_approve=auto_approve)

# ── Audit ─────────────────────────────────────────────────────────────────────

@cli.command()
def audit():
    """Run Indian compliance audit (DPDP Act 2023, RBI, GST, Aadhaar/PAN)."""
    show_banner()
    console.print("\n[bold magenta]Indian Compliance Audit[/bold magenta]\n")
    task = """Audit this project for Indian regulatory compliance.

Check:
1. **DPDP Act 2023**: user consent mechanism, data encryption at rest+transit,
   right-to-deletion mechanism, privacy policy present, no unnecessary data collection
2. **Payments (if present)**: RBI PPI guidelines, no raw card data stored,
   HTTPS enforced, no storing CVV
3. **GST (if present)**: correct CGST/SGST/IGST split logic, correct tax rates (5/12/18/28%),
   HSN/SAC codes present
4. **Aadhaar/PAN (if present)**: UIDAI guidelines, Aadhaar not stored in plain text,
   masked display (last 4 digits only)
5. **General security**: hardcoded secrets, SQL injection, XSS, insecure dependencies

Produce a compliance report with severity ratings:
## CRITICAL (immediate action required)
## HIGH (fix before production)
## MEDIUM (should fix)
## LOW (nice to fix)
## COMPLIANT (things done right)"""
    run_agent(task, os.getcwd())

# ── Ask ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("question")
@click.option("--file", "-f", help="Specific file to ask about")
def ask(question, file):
    """Ask a question about your codebase.

    \b
    Examples:
      bharatcode ask "how does auth work?"
      bharatcode ask "what does this do" -f src/payment.py
    """
    show_banner()
    task = f"Read '{file}' then answer: {question}" if file else question
    run_agent(task, os.getcwd())

# ── Explain ───────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("file")
def explain(file):
    """Explain what a file does in plain English.

    \b
    Example:
      bharatcode explain src/payment/razorpay.py
    """
    show_banner()
    task = f"""Read and explain: {file}

Provide:
1. What this file does (1-2 sentence overview)
2. Key functions/classes and their purpose
3. How data flows through this file
4. How it connects to the rest of the project
5. Any potential issues or improvements"""
    run_agent(task, os.getcwd())

# ── Refactor ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("file")
@click.option("--reason", "-r", help="Why to refactor")
@click.option("--auto-approve", "-y", is_flag=True)
def refactor(file, reason, auto_approve):
    """Refactor code for better quality.

    \b
    Example:
      bharatcode refactor src/utils.py
      bharatcode refactor src/auth.py -r "split into smaller functions"
    """
    show_banner()
    task = f"""Refactor: {file}
{"Reason: " + reason if reason else "General quality improvement"}

Goals:
- Readability and clarity
- Single responsibility principle
- DRY (Don't Repeat Yourself)
- Better naming
- Reduce complexity

IMPORTANT: Keep identical functionality. Run tests after refactoring."""
    run_agent(task, os.getcwd(), auto_approve=auto_approve)

# ── New: website / app scaffolding ───────────────────────────────────────────

@cli.group()
def new():
    """Scaffold a new website or app from scratch.

    \b
    Examples:
      bharatcode new website "My Portfolio"
      bharatcode new website "Startup Landing"
      bharatcode new app "Task Manager" --type flask
      bharatcode new app "Dashboard" --type react
      bharatcode new app "SaaS Platform" --type fullstack
    """
    pass


@new.command("website")
@click.argument("name")
@click.argument("description", default="")
@click.option("--dir", "-d", "output_dir", default=None,
              help="Output directory (default: ./<name>)")
@click.option("--auto-approve", "-y", is_flag=True)
def new_website(name, description, output_dir, auto_approve):
    """Create a complete website from scratch.

    \b
    Examples:
      bharatcode new website "Chhelu Portfolio"
      bharatcode new website "GreenTech India" "solar energy startup landing page"
      bharatcode new website "Bistro Kitchen" "restaurant website with menu and reservations"
    """
    from .skills import get_skill
    show_banner()

    dest_path = os.path.abspath(output_dir or name.lower().replace(" ", "-"))
    os.makedirs(dest_path, exist_ok=True)

    console.print(f"\n[bold green]Building website:[/bold green] {name}")
    if description:
        console.print(f"[dim]{description}[/dim]")
    console.print(f"[dim]Output: {dest_path}[/dim]\n")

    skill_prompt = get_skill("newsite")
    task = f"""{skill_prompt}

PROJECT DETAILS:
- Name: {name}
- Description: {description or "A modern, beautiful website"}
- Output folder (absolute): {dest_path}

CRITICAL PATH RULE: Every file MUST be written inside {dest_path}.
Use the full absolute path in every <<<FILE:>>> marker and every write_file call.
  Correct:   <<<FILE:{dest_path}/index.html>>>
  Correct:   <<<FILE:{dest_path}/css/variables.css>>>
  WRONG:     <<<FILE:index.html>>>   ← relative paths go to wrong place

Start by thinking about what kind of website this is and what it needs.
Then design the file structure, then write every file completely."""

    run_agent(task, project_path=dest_path, auto_approve=auto_approve)

    # Safety net: git init + initial commit so every later change is revertable
    from .agent import _git_checkpoint
    _h = _git_checkpoint(dest_path, f"initial scaffold of {name}", init=True)
    if _h:
        console.print(f"\n[dim]📌 Initial git commit [cyan]{_h}[/cyan] — use git diff / git checkout to review or revert changes[/dim]")


@new.command("app")
@click.argument("name")
@click.argument("description", default="")
@click.option("--type", "-t", "app_type",
              type=click.Choice(["flask", "react", "fullstack", "node", "nextjs"], case_sensitive=False),
              default=None,
              help="App type: flask | react | fullstack | node | nextjs")
@click.option("--dir", "-d", "output_dir", default=None,
              help="Output directory (default: ./<name>)")
@click.option("--auto-approve", "-y", is_flag=True)
def new_app(name, description, app_type, output_dir, auto_approve):
    """Create a complete application from scratch.

    \b
    Examples:
      bharatcode new app "TaskFlow" "kanban task manager with teams"
      bharatcode new app "ShopIndia" "e-commerce with Razorpay" --type flask
      bharatcode new app "AnalyticsPro" "data dashboard" --type react
      bharatcode new app "StartupOS" "SaaS platform" --type fullstack
    """
    from .skills import get_skill
    show_banner()

    dest_path    = os.path.abspath(output_dir or name.lower().replace(" ", "-"))
    type_display = app_type or "auto-detect from description"
    os.makedirs(dest_path, exist_ok=True)

    console.print(f"\n[bold green]Building app:[/bold green] {name}")
    if description:
        console.print(f"[dim]{description}[/dim]")
    console.print(f"[dim]Type: {type_display}  |  Output: {dest_path}[/dim]\n")

    skill_prompt = get_skill("newapp")

    type_hint = ""
    if app_type:
        stack_hints = {
            "flask":     "Python + Flask (REST API + Jinja2 templates, SQLAlchemy, Flask-Login)",
            "react":     "React 18 + Vite + React Router 6 (pure frontend SPA)",
            "fullstack": "Flask REST API (port 5000) + React Vite frontend (port 5173), flask-cors configured, Vite proxy set up, .env files for both sides",
            "node":      "Node.js + Express (REST API, JWT auth, Mongoose/Prisma)",
            "nextjs":    "Next.js 14 App Router (SSR/SSG, server components, API routes)",
        }
        type_hint = f"\nTECH STACK: {stack_hints[app_type.lower()]}"

    task = f"""{skill_prompt}

PROJECT DETAILS:
- Name: {name}
- Description: {description or "A modern web application"}
- Output folder (absolute): {dest_path}{type_hint}

CRITICAL PATH RULE: Every file MUST be written inside {dest_path}.
Use the full absolute path in every <<<FILE:>>> marker and every write_file call.
  Correct:   <<<FILE:{dest_path}/app.py>>>
  Correct:   <<<FILE:{dest_path}/frontend/src/App.jsx>>>
  WRONG:     <<<FILE:app.py>>>   ← relative paths go to wrong place

{"If no stack is specified, choose the best one for this specific project based on the description." if not app_type else ""}

Start by analyzing what the app does, design the data models and architecture, then write every file completely."""

    run_agent(task, project_path=dest_path, auto_approve=auto_approve)

    # Safety net: git init + initial commit so every later change is revertable
    from .agent import _git_checkpoint
    _h = _git_checkpoint(dest_path, f"initial scaffold of {name}", init=True)
    if _h:
        console.print(f"\n[dim]📌 Initial git commit [cyan]{_h}[/cyan] — use git diff / git checkout to review or revert changes[/dim]")


# ── Init ──────────────────────────────────────────────────────────────────────

@cli.command()
def init():
    """Initialize Sylithe Code in your project (creates BHARATCODE.md)."""
    p = Path("BHARATCODE.md")
    if p.exists():
        show_warning("BHARATCODE.md already exists.")
        return

    name  = Prompt.ask("[bold]Project name[/bold]")
    stack = Prompt.ask("[bold]Tech stack[/bold]", default="Python/Flask")

    p.write_text(f"""# {name} — Sylithe Code Config

## Tech Stack
{stack}

## Project Description
<!-- Describe your project here -->

## Coding Standards
- Follow PEP8 for Python / Google Style for Java / StandardJS for JS
- Write tests for all new features
- Use type hints (Python) or type annotations (TypeScript)

## Indian Integrations
<!-- List any Indian APIs: Razorpay, UIDAI, GST APIs, DigiLocker, etc. -->

## Run Commands
- Tests: `pytest` (or `npm test` / `mvn test`)
- Start: `python app.py` (or `npm start`)
- Lint: `flake8` (or `eslint .`)

## Notes for Sylithe Code
<!-- Special instructions for the AI agent -->
- Never commit .env files
- Always run tests after fixes
""", encoding="utf-8")

    show_success(f"Created BHARATCODE.md for {name}")
    show_info("Edit BHARATCODE.md to add project-specific instructions.")

# ── Paste-aware input ─────────────────────────────────────────────────────────

def _print_mode(task: str | None, auto_approve: bool, model: str | None):
    """Non-interactive mode: run once and print the final answer, then exit.

    Usage:
      bharatcode --print "explain this"
      bharatcode -p "fix this bug" < src/app.py
      cat error.log | bharatcode -p "what is wrong here"
    """
    from .config import save_config, load_config, MODEL_API_MAP, MODEL_ALIASES

    # ── Override model for this run if -m was passed ──────────────────────────
    if model:
        cfg = load_config()
        api_model = MODEL_API_MAP.get(model) or MODEL_ALIASES.get(model) or model
        cfg["model"] = api_model
        save_config(cfg)

    # ── Build the task string ─────────────────────────────────────────────────
    stdin_content = ""
    if not sys.stdin.isatty():
        stdin_content = sys.stdin.read()

    if not task and not stdin_content:
        click.echo(
            "Usage: bharatcode --print \"your task\"\n"
            "       cat file.py | bharatcode -p \"explain this\"\n"
            "       bharatcode -p \"fix the bug\" < src/app.py",
            err=True,
        )
        sys.exit(1)

    if stdin_content and task:
        full_task = f"{task}\n\n--- stdin ---\n{stdin_content}"
    elif stdin_content:
        full_task = stdin_content
    else:
        full_task = task

    # ── Run agent once, capture output ────────────────────────────────────────
    cwd = os.getcwd()
    system_content = _build_system(cwd)

    output = run_agent(
        full_task,
        project_path=cwd,
        auto_approve=auto_approve,
        system_content=system_content,
        silent=True,
    )

    if output:
        print(output)


def _read_input() -> str:
    """
    Read one user turn with paste detection.

    When the user pastes multi-line text the terminal buffers all lines but
    only delivers them one-at-a-time through input().  We detect this by
    checking whether more data is waiting in the input buffer immediately
    after reading the first line:

      Windows — msvcrt.kbhit() returns True while console input is buffered.
      Unix    — select() with timeout=0 tells us stdin has more bytes ready.

    Normal typing never triggers this because the buffer is empty between
    keystrokes.  A pasted block arrives all at once, so every subsequent line
    is found waiting and gets merged.
    """
    # Print styled prompt, then read first line via plain input() so we can
    # inspect the buffer ourselves afterwards.
    console.print("[bold green]>[/bold green] ", end="", highlight=False)
    try:
        first = input()
    except (EOFError, KeyboardInterrupt):
        raise

    lines = [first]

    try:
        if sys.platform == "win32":
            import msvcrt, time
            time.sleep(0.030)          # let the paste buffer fill
            while msvcrt.kbhit():
                try:
                    lines.append(input())
                except EOFError:
                    break
                time.sleep(0.010)
        else:
            import select, time
            time.sleep(0.030)
            while select.select([sys.stdin], [], [], 0)[0]:
                try:
                    lines.append(sys.stdin.readline().rstrip("\n"))
                except EOFError:
                    break
    except Exception:
        pass  # any failure → return what we have

    return "\n".join(lines)


# ── Coordinator Notification Loop ─────────────────────────────────────────────

def _coordinator_notification_loop(session, cwd, auto_approve, history):
    """
    After each coordinator turn, workers may still be running in background threads.
    This loop polls the WorkerPool's notification_queue and re-enters run_agent()
    (with empty task = no new user message) whenever a worker completes.

    Exits only when: no workers are running AND notification queue is empty.
    Ctrl+C to interrupt early.
    """
    import time
    from .coordinator import RUNNING as W_RUNNING

    pool = session.get("worker_pool")
    if not pool:
        return

    console.print("[dim]  ⏳ Workers running — will synthesize when they report back. Ctrl+C to interrupt.[/dim]")

    try:
        accumulated = 0  # total notifications waiting in history

        while True:
            time.sleep(0.4)

            with pool._lock:
                running_count = sum(
                    1 for w in pool._workers.values()
                    if w.status == W_RUNNING
                )

            # Drain any new notifications — accumulate silently, don't synthesize yet
            notifications = pool.drain_notifications()
            if notifications:
                for n in notifications:
                    history.append(n)
                accumulated += len(notifications)
                console.print(
                    f"  [dim]📬 {len(notifications)} worker(s) reported — "
                    f"{running_count} still running...[/dim]"
                )

            # Still have workers running — keep accumulating
            if running_count > 0:
                continue

            # All workers done — do one final drain to catch any last-second results
            final = pool.drain_notifications()
            for n in final:
                history.append(n)
                accumulated += 1

            # No accumulated results at all — nothing to synthesize
            if accumulated == 0:
                break

            # ── Synthesize ONCE with every worker result in history ──────────
            console.print(
                f"\n  [bold cyan]🎯 All {accumulated} worker result{'s' if accumulated > 1 else ''} in"
                f" — synthesizing...[/bold cyan]\n"
            )
            accumulated = 0  # reset before synthesis (coordinator may spawn new workers)

            try:
                run_agent(
                    "",
                    project_path=cwd,
                    auto_approve=session.get("auto_approve", auto_approve),
                    history=history,
                    system_content=session.get("system"),
                    file_cache=session.get("file_cache"),
                    worker_pool=pool,
                    change_log=session.get("change_log"),
                )
            except Exception as e:
                console.print(f"\n  [dim red]⚠  Coordinator synthesis error: {e}[/dim red]")

            # After synthesis the coordinator may have spawned new workers.
            # Loop back — if new workers exist we accumulate again; otherwise we exit.

    except KeyboardInterrupt:
        with pool._lock:
            still = sum(1 for w in pool._workers.values() if w.status == W_RUNNING)
        console.print(f"\n[dim]  Interrupted. {still} worker(s) still running in background.[/dim]")
        console.print("[dim]  Their results arrive automatically on your next message. /workers for status.[/dim]")


# ── Interactive Mode ──────────────────────────────────────────────────────────

def interactive_mode(auto_approve: bool = False):
    from . import session_storage

    show_banner()
    cwd = os.getcwd()

    console.print(f"[dim]Working directory: {cwd}[/dim]")

    # ── Session resume ────────────────────────────────────────────────────────
    conversation_history: list = []
    _sess_id    = session_storage.new_session_id()
    _sess_path  = session_storage.session_path(cwd, _sess_id)
    _last_saved = 0   # tracks how many messages have been flushed to disk

    recent = session_storage.list_recent(cwd, max_n=3)
    if recent:
        prev = recent[0]
        console.print(
            f"[dim]Previous session found: {prev['turns']} turns, "
            f"{prev['mtime_str']} — last: \"{prev['last_message']}\"[/dim]"
        )
        console.print("[dim]  /resume to continue it, or start fresh below.[/dim]")

    console.print("[dim]Type your task below. /help for commands. Ctrl+C to exit.[/dim]\n")

    # Build system prompt ONCE for the whole session — not per turn
    system_content = _build_system(cwd)

    file_cache:  dict = {}
    change_log:  dict = {}
    todo_list:   list = []
    session = {
        "messages":      conversation_history,
        "file_cache":    file_cache,
        "change_log":    change_log,
        "todo_list":     todo_list,
        "auto_approve":  auto_approve,
        "plan_mode":     False,
        "system":        system_content,
        # session storage context — used by /resume command
        "_sess_id":      _sess_id,
        "_sess_path":    _sess_path,
        "_recent":       recent,
    }

    # Write session pointer so /resume can find it
    session_storage.save_latest_pointer(cwd, _sess_id)

    # Enable readline history if available (Unix/Mac)
    history_file = Path.home() / ".bharatcode" / "history"
    try:
        if readline is not None:
            history_file.parent.mkdir(exist_ok=True)
            if history_file.exists():
                readline.read_history_file(str(history_file))
            readline.set_history_length(500)
    except Exception:
        pass

    while True:
        try:
            console.print()
            raw = _read_input()

            if not raw.strip():
                continue

            if raw.lower() in ("exit", "quit", "q", ":q"):
                _cl = session.get("change_log", {})
                if _cl:
                    console.print(f"\n[dim]Session changes ({len(_cl)} file{'s' if len(_cl) > 1 else ''}):[/dim]")
                    for _fp in sorted(_cl.keys()):
                        _st = _cl[_fp]
                        _total = _st.get("writes", 0) + _st.get("edits", 0)
                        _parts = []
                        if _st.get("writes"):
                            _parts.append(f"{_st['writes']}W")
                        if _st.get("edits"):
                            _parts.append(f"{_st['edits']}E")
                        console.print(
                            f"  [dim cyan]{_fp}[/dim cyan] [dim]({', '.join(_parts)})[/dim]"
                        )
                console.print("\n[dim]Goodbye! Happy coding![/dim]\n")
                break

            # Save history
            try:
                if readline is not None:
                    readline.write_history_file(str(history_file))
            except Exception:
                pass

            # Slash commands
            if raw.startswith("/"):
                handle_slash_command(raw, session)
                continue

            # Run agent — cached system prompt + persistent history + plan mode
            console.print()
            plan_mode = session.get("plan_mode", False)
            if plan_mode:
                console.print("[dim yellow]  [PLAN MODE — read only][/dim yellow]\n")
            _paths_before = set(session.get("change_log", {}).keys())
            run_agent(
                raw,
                project_path=cwd,
                auto_approve=session.get("auto_approve", auto_approve),
                history=conversation_history,
                system_content=session.get("system"),
                plan_mode=plan_mode,
                file_cache=session.get("file_cache"),
                worker_pool=session.get("worker_pool"),
                change_log=session.get("change_log"),
                todo_state=session.get("todo_list"),
            )

            # ── Flush new messages to JSONL session file ─────────────────────
            new_msgs = conversation_history[_last_saved:]
            if new_msgs:
                session_storage.append_messages(_sess_path, new_msgs)
                _last_saved = len(conversation_history)

            # ── Refresh the project index when new files were created ────────
            # (the index is baked into the system prompt at session start; a
            # build that adds files would otherwise leave the model blind to them)
            if set(session.get("change_log", {}).keys()) - _paths_before:
                try:
                    session["system"] = _build_system(cwd)
                except Exception:
                    pass

            # ── Coordinator: wait for workers and re-enter when they report ──
            # run_agent() exits as soon as the coordinator's turn ends.
            # Workers run in background threads and push <task-notification>
            # to the queue. We poll here and re-call run_agent() (with empty
            # task so no new user message is added) whenever results arrive.
            if session.get("coordinator_mode"):
                _coordinator_notification_loop(
                    session, cwd, auto_approve, conversation_history
                )

        except KeyboardInterrupt:
            console.print("\n[dim]Use 'exit' or Ctrl+D to quit.[/dim]")
        except EOFError:
            console.print("\n[dim]Goodbye![/dim]\n")
            break
        except Exception as e:
            show_error(str(e))
            if os.getenv("BHARATCODE_DEBUG"):
                import traceback; traceback.print_exc()
