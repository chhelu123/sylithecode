"""
Persistent memory — stores facts across sessions in structured Markdown files.
Inspired by Claude Code's auto-memory system (MEMORY.md index + topic files).

Storage layout:
  ~/.bharatcode/memory/
    MEMORY.md              ← index, always loaded into every system prompt
    file.md                ← memories tagged "file" (file paths, contents)
    project.md             ← memories tagged "project" (architecture, decisions)
    user.md                ← memories tagged "user" (preferences, background)
    feedback.md            ← memories tagged "feedback" (what to do/avoid)
    general.md             ← everything else
    <any_custom_tag>.md    ← agent can use any tag

Each entry in a topic file:
  - [id=42] [2026-06-08 10:22] content here

MEMORY.md is a one-line-per-entry index pointing to topic files.
It is loaded verbatim into the system prompt so the model always knows what exists.
Topic files are loaded fully (budget-capped at ~6K chars total) alongside the index.
"""
import json
from datetime import datetime
from pathlib import Path

MEMORY_DIR    = Path.home() / ".bharatcode" / "memory"
MEMORY_INDEX  = MEMORY_DIR / "MEMORY.md"
_COUNTER_FILE = MEMORY_DIR / ".counter"
_OLD_JSON     = Path.home() / ".bharatcode" / "memory.json"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ensure() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if not MEMORY_INDEX.exists():
        if _OLD_JSON.exists():
            _migrate_from_json()
        else:
            MEMORY_INDEX.write_text("# Memory Index\n\n", encoding="utf-8")


def _next_id() -> int:
    """Global auto-increment counter for memory entry IDs."""
    try:
        n = int(_COUNTER_FILE.read_text(encoding="utf-8").strip()) + 1
    except Exception:
        n = 1
    _COUNTER_FILE.write_text(str(n), encoding="utf-8")
    return n


def _tag_file(tag: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag.lower())
    return MEMORY_DIR / f"{safe}.md"


def _rebuild_index() -> None:
    """Rewrite MEMORY.md from the current set of topic files."""
    lines = ["# Memory Index\n"]
    for md in sorted(MEMORY_DIR.glob("*.md")):
        if md.name == "MEMORY.md":
            continue
        try:
            content = md.read_text(encoding="utf-8")
            # first bullet line as description
            preview = next(
                (l.strip().lstrip("- ").split("] ", 2)[-1]   # strip [id=N] [ts]
                 for l in content.splitlines()
                 if l.strip().startswith("- [")),
                md.stem,
            )[:120]
            lines.append(f"- [{md.stem}]({md.name}) — {preview}")
        except Exception:
            lines.append(f"- [{md.stem}]({md.name})")
    MEMORY_INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _migrate_from_json() -> None:
    """
    One-time migration: flat memory.json → individual topic .md files.
    Preserves all entries and assigns sequential IDs.
    """
    try:
        old = json.loads(_OLD_JSON.read_text(encoding="utf-8"))
    except Exception:
        old = []

    # Group by tag
    by_tag: dict[str, list[tuple[int, str, str]]] = {}
    counter = 0
    for m in old:
        counter += 1
        tag     = m.get("tag", "general")
        content = (m.get("text") or m.get("content") or "").strip()
        ts      = (m.get("created") or "")[:16] or "migrated"
        if content:
            by_tag.setdefault(tag, []).append((counter, ts, content))

    for tag, entries in by_tag.items():
        lines = [f"# {tag.title()}\n"]
        for mid, ts, content in entries:
            lines.append(f"- [id={mid}] [{ts}] {content}")
        _tag_file(tag).write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Set counter to max ID used
    _COUNTER_FILE.write_text(str(counter), encoding="utf-8")
    _rebuild_index()

    # Back up old file
    try:
        _OLD_JSON.rename(_OLD_JSON.with_suffix(".json.bak"))
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def add_memory(text: str, tag: str = "general") -> dict:
    """Add a memory entry. Returns dict with id, text, tag, created.
    Exact-duplicate facts in the same topic file are skipped — the agent
    saves memory after every task, so without this the store fills with
    repeats that crowd real facts out of the context budget."""
    _ensure()
    ts  = datetime.now().isoformat()
    ts_short = ts[:16]
    text = text.strip()

    f = _tag_file(tag)
    if f.exists() and text:
        try:
            if text in f.read_text(encoding="utf-8"):
                return {"id": -1, "text": text, "tag": tag, "created": ts, "duplicate": True}
        except Exception:
            pass

    mid = _next_id()
    if not f.exists():
        f.write_text(f"# {tag.title()}\n\n", encoding="utf-8")
    with open(f, "a", encoding="utf-8") as fp:
        fp.write(f"- [id={mid}] [{ts_short}] {text}\n")

    _rebuild_index()
    return {"id": mid, "text": text, "tag": tag, "created": ts}


def delete_memory(memory_id: int) -> bool:
    """Delete a memory entry by its ID. Returns True if found and deleted."""
    _ensure()
    marker = f"[id={memory_id}]"
    for md in MEMORY_DIR.glob("*.md"):
        if md.name == "MEMORY.md":
            continue
        try:
            content = md.read_text(encoding="utf-8")
            if marker not in content:
                continue
            lines = content.splitlines(keepends=True)
            new_lines = [l for l in lines if marker not in l]
            md.write_text("".join(new_lines), encoding="utf-8")
            _rebuild_index()
            return True
        except Exception:
            pass
    return False


def load_memories() -> list[dict]:
    """Backward compat — return flat list of {id, text, tag, created} dicts."""
    _ensure()
    result = []
    for md in sorted(MEMORY_DIR.glob("*.md")):
        if md.name == "MEMORY.md":
            continue
        tag = md.stem
        try:
            for line in md.read_text(encoding="utf-8").splitlines():
                if not line.strip().startswith("- [id="):
                    continue
                # Parse: - [id=N] [YYYY-MM-DD HH:MM] content
                rest = line.lstrip("- ")
                # Extract id
                id_end = rest.find("]")
                try:
                    mid = int(rest[4:id_end])   # rest starts with "[id=N]"
                except Exception:
                    mid = 0
                rest = rest[id_end + 2:].strip()   # skip "] "
                # Extract timestamp
                if rest.startswith("["):
                    ts_end = rest.find("]")
                    ts = rest[1:ts_end]
                    rest = rest[ts_end + 2:].strip()
                else:
                    ts = ""
                result.append({"id": mid, "text": rest, "tag": tag, "created": ts})
        except Exception:
            pass
    return result


def remember(text: str, tag: str = "project") -> str:
    """Tool-callable: save a memory and return confirmation."""
    entry = add_memory(text, tag)
    if entry.get("duplicate"):
        return f"Already in memory — skipped duplicate: {text[:80]}"
    return f"Memory saved (id={entry['id']}): {text[:80]}"


def memories_to_context() -> str:
    """
    Build the memory section injected into the system prompt.
    Always includes MEMORY.md index. Then loads all topic files
    (newest first) up to a ~6K char budget so large memory stores
    don't flood the context.
    """
    _ensure()
    if not MEMORY_INDEX.exists():
        return ""

    index = MEMORY_INDEX.read_text(encoding="utf-8").strip()
    if not index or index == "# Memory Index":
        return ""

    parts = [f"\n\n## Persistent Memory (from past sessions)\n\n{index}"]

    # Load topic files up to budget — NEWEST entries first within each file,
    # so when the budget cuts anything it always cuts the OLDEST facts.
    # (Topic files are append-only: newest entries live at the bottom.)
    budget = 6000
    for md in sorted(MEMORY_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        if md.name == "MEMORY.md":
            continue
        if budget <= 0:
            break
        try:
            entry_lines = [
                l for l in md.read_text(encoding="utf-8").splitlines()
                if l.strip().startswith("- [id=")
            ]
            if not entry_lines:
                continue
            entry_lines = entry_lines[::-1][:40]   # newest first, max 40 per topic
            chunk = f"\n\n### {md.stem} (newest first)\n" + "\n".join(entry_lines)
            if len(chunk) > budget:
                chunk = chunk[:budget] + "\n  ...(older entries omitted)"
            parts.append(chunk)
            budget -= len(chunk)
        except Exception:
            pass

    return "".join(parts)


def show_memories(console) -> None:
    """Pretty-print all memory entries for /memory list."""
    _ensure()
    files = [f for f in sorted(MEMORY_DIR.glob("*.md")) if f.name != "MEMORY.md"]

    if not files:
        console.print("[dim]No memories saved yet. The agent saves memories automatically, "
                      "or use: /memory add <text>[/dim]")
        return

    console.print(f"\n[bold]Memory[/bold]  [dim]{MEMORY_DIR}[/dim]\n")
    total = 0
    for md in files:
        try:
            content = md.read_text(encoding="utf-8").strip()
            entries = [l for l in content.splitlines() if l.strip().startswith("- [id=")]
            if not entries:
                continue
            console.print(f"  [bold cyan]{md.stem}[/bold cyan]  [dim]({len(entries)} entries)[/dim]")
            for line in entries:
                # Parse display: strip [id=N] prefix, keep [ts] and content
                rest = line.lstrip("- ")
                id_end = rest.find("]")
                mid = rest[4:id_end] if rest.startswith("[id=") else "?"
                rest = rest[id_end + 2:].strip()
                console.print(f"    [dim]{mid:>4}[/dim]  {rest}")
                total += 1
            console.print()
        except Exception:
            pass

    console.print(f"[dim]  {total} total entries — /memory del <id> to remove[/dim]\n")
