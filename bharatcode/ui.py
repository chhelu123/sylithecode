"""Terminal UI using Rich — inspired by Claude Code's clean terminal interface."""
import sys
import io
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich.rule import Rule
from rich import box

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console(highlight=False)

TOOL_ICONS = {
    "bash":       "[yellow]⚡[/yellow]",
    "read_file":  "[blue]📖[/blue]",
    "write_file": "[green]✏️[/green]",
    "edit_file":  "[cyan]🔧[/cyan]",
    "glob":       "[magenta]📁[/magenta]",
    "grep":       "[white]🔍[/white]",
}

def show_banner():
    console.print(Panel(
        "[bold green]Sylithe Code[/bold green]  [dim]AI Coding Agent for Indian Developers[/dim]\n"
        "[dim]Powered by Sylithe  |  Type /help for commands  |  Ctrl+C to exit[/dim]",
        border_style="green",
        padding=(0, 2),
    ))

def show_tool_call(name: str, args: dict):
    icon = TOOL_ICONS.get(name, "[dim]🔧[/dim]")
    first_arg = list(args.values())[0] if args else ""
    preview = str(first_arg)[:80].replace("\n", "↵")
    console.print(f"  {icon} [dim]{name}[/dim]  [cyan]{preview}[/cyan]")

def show_response(text: str):
    console.print(Panel(
        Markdown(text),
        border_style="green",
        title="[bold green]Sylithe Code[/bold green]",
        padding=(0, 1),
    ))

def show_error(msg: str):
    console.print(f"[red]✗[/red]  {msg}")

def show_success(msg: str):
    console.print(f"[green]✓[/green]  {msg}")

def show_warning(msg: str):
    console.print(f"[yellow]⚠[/yellow]   {msg}")

def show_info(msg: str):
    console.print(f"[blue]ℹ[/blue]   {msg}")

def show_separator():
    console.print(Rule(style="dim"))

def spinner(message: str):
    return console.status(f"[green]{message}[/green]", spinner="dots")

def ask_input(prompt_text: str = "") -> str:
    return Prompt.ask(f"[bold green]>[/bold green] {prompt_text}")

def confirm(msg: str) -> bool:
    return Confirm.ask(f"[yellow]{msg}[/yellow]")
