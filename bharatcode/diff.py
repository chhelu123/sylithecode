"""
Diff display — inspired by Claude Code's StructuredDiff + FileEditToolDiff components.
Shows colored unified diffs when files are written or edited.
"""
import difflib
from pathlib import Path
from rich.console import Console
from rich.text import Text
from rich.panel import Panel

console = Console()

def show_file_diff(path: str, old_content: str, new_content: str):
    """Show a colored unified diff between old and new file content."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    ))

    if not diff:
        return

    rendered = Text()
    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            rendered.append(line + "\n", style="bold white")
        elif line.startswith("@@"):
            rendered.append(line + "\n", style="cyan")
        elif line.startswith("+"):
            rendered.append(line + "\n", style="green")
        elif line.startswith("-"):
            rendered.append(line + "\n", style="red")
        else:
            rendered.append(line + "\n", style="dim")

    added   = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))

    title = (
        f"[white]{path}[/white]  "
        f"[green]+{added}[/green] [red]-{removed}[/red]"
    )
    try:
        console.print(Panel(rendered, title=title, border_style="dim", padding=(0, 1)))
    except UnicodeEncodeError:
        # Terminal can't render the characters (e.g. emoji on Windows cp1252).
        # Show a plain summary and keep going — never stop the agent.
        console.print(
            f"  [dim]{path}  [green]+{added}[/green] [red]-{removed}[/red]"
            f"  (diff hidden — file contains Unicode/emoji)[/dim]"
        )

def capture_write(path: str, new_content: str, mode: str = "w") -> str:
    """Write file with diff display. Supports mode='w' (overwrite) or 'a' (append)."""
    if not path or not path.strip():
        return (
            "Error: 'path' is empty. Provide the full file path. "
            "For large files write in chunks using mode='a' to append."
        )
    if path.strip() in (".", "./", "/", "\\"):
        return "Error: 'path' must be a file path like 'C:/chhelu 1/analysis/dashboard.html', not a directory."
    p = Path(path)
    if p.exists() and p.is_dir():
        return f"Error: '{path}' is a directory. Provide a full file path including filename."

    from .tools import _read_text_safe
    old = _read_text_safe(p) if p.exists() else ""
    p.parent.mkdir(parents=True, exist_ok=True)
    write_mode = "a" if mode == "a" else "w"
    with open(p, write_mode, encoding="utf-8") as f:
        f.write(new_content)
    full = _read_text_safe(p)
    if old != full:
        show_file_diff(path, old, full)
    total_lines = len(full.splitlines())
    action = "Appended to" if write_mode == "a" else "Written"
    return f"{action} {path} ({total_lines} lines total)"

def capture_edit(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Perform edit via tools.edit_file (CRLF-safe, with nearest-match hints
    on failure) and show a colored diff on success."""
    from .tools import edit_file as _edit_impl, _read_text_safe

    if not path or not path.strip():
        return (
            "Error: 'path' is empty. Provide the full file path. "
            "Example: edit_file(path='C:/chhelu 1/analysis/site3_report.html', old_string='...', new_string='...')"
        )
    p = Path(path)
    old_content = None
    if p.exists() and p.is_file():
        try:
            old_content = _read_text_safe(p)
        except Exception:
            old_content = None

    result = _edit_impl(path, old_string, new_string, replace_all=replace_all)

    if (old_content is not None
            and not result.startswith("Error")
            and not result.startswith("File not found")):
        try:
            new_content = _read_text_safe(p)
            if new_content != old_content:
                show_file_diff(path, old_content, new_content)
        except Exception:
            pass
    return result
