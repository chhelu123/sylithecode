"""
Permission system — inspired by Claude Code's BashPermissionRequest component.
Ask user allow/deny/always before running bash commands.
"""
import json
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()

_SESSION_ALWAYS: set[str] = set()   # commands allowed for this session
_ALWAYS_FILE = Path.home() / ".bharatcode" / "always_allow.json"

def _load_always() -> set[str]:
    if _ALWAYS_FILE.exists():
        try:
            return set(json.loads(_ALWAYS_FILE.read_text()))
        except Exception:
            return set()
    return set()

def _save_always(items: set[str]):
    _ALWAYS_FILE.parent.mkdir(exist_ok=True)
    _ALWAYS_FILE.write_text(json.dumps(sorted(items)))

_PERMANENT_ALWAYS: set[str] = _load_always()

def _command_key(cmd: str) -> str:
    """Normalize a command for matching (first word / verb)."""
    return cmd.strip().split()[0] if cmd.strip() else ""

def needs_approval(tool_name: str, args: dict, auto_approve: bool = False) -> tuple[bool, str]:
    """
    Returns (approved, reason).
    Safe read-only tools are auto-approved.
    Bash requires user confirmation unless always-allowed.
    """
    if auto_approve:
        return True, "auto"

    # Read-only tools: always OK
    if tool_name in ("read_file", "glob", "grep"):
        return True, "safe"

    # write/edit — auto-approve (user sees the diff anyway)
    if tool_name in ("write_file", "edit_file"):
        return True, "safe"

    # bash — check allow lists
    if tool_name == "bash":
        cmd = args.get("command", "")
        key = _command_key(cmd)
        if key in _PERMANENT_ALWAYS or key in _SESSION_ALWAYS:
            return True, "always-allowed"
        return False, "needs-approval"

    return True, "safe"

def ask_permission(tool_name: str, args: dict) -> bool:
    """
    Show a permission dialog for bash commands.
    Returns True if approved.
    """
    cmd = args.get("command", "")

    console.print()
    console.print(Panel(
        f"[bold yellow]{cmd}[/bold yellow]",
        title="[bold red] BharatCode wants to run a command [/bold red]",
        border_style="yellow",
        padding=(0, 1),
    ))
    console.print(
        "  [green]y[/green] Allow once  "
        "[cyan]s[/cyan] Allow for session  "
        "[blue]a[/blue] Always allow  "
        "[red]n[/red] Deny"
    )

    choice = Prompt.ask("  [dim]Permission[/dim]", choices=["y", "s", "a", "n"], default="y")

    if choice == "n":
        console.print("  [red]Denied.[/red]")
        return False

    key = _command_key(cmd)

    if choice == "s":
        _SESSION_ALWAYS.add(key)
        console.print(f"  [cyan]Allowed for this session: {key}[/cyan]")
    elif choice == "a":
        _PERMANENT_ALWAYS.add(key)
        _save_always(_PERMANENT_ALWAYS)
        console.print(f"  [blue]Always allowed: {key}[/blue]")

    console.print()
    return True
