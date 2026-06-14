"""
Session persistence — saves conversation history to JSONL as it happens,
so sessions survive process crashes and can be resumed in a new terminal.

Storage layout:
  ~/.bharatcode/sessions/<project_hash>/<session_id>.jsonl
  ~/.bharatcode/sessions/<project_hash>/latest          ← ID of most recent session

One JSON object per line. Messages appended in real-time so a crash mid-turn
only loses the in-progress messages, not the whole session.
"""
import json
import uuid
import hashlib
from pathlib import Path
from typing import Optional

_SESSIONS_ROOT = Path.home() / ".bharatcode" / "sessions"


# ── Path helpers ──────────────────────────────────────────────────────────────

def _project_dir(project_path: str) -> Path:
    h = hashlib.md5(str(project_path).encode()).hexdigest()[:10]
    d = _SESSIONS_ROOT / h
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def session_path(project_path: str, session_id: str) -> Path:
    return _project_dir(project_path) / f"{session_id}.jsonl"


# ── Read / Write ──────────────────────────────────────────────────────────────

def append_messages(path: Path, messages: list[dict]) -> None:
    """
    Append messages to the JSONL file. Fire-and-forget — never raises.
    Called by interactive_mode after each agent turn completes.
    """
    if not messages:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_messages(path: Path) -> list[dict]:
    """Load all valid messages from a JSONL session file."""
    if not path or not path.exists():
        return []
    messages = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return messages


# ── Session discovery ─────────────────────────────────────────────────────────

def save_latest_pointer(project_path: str, session_id: str) -> None:
    (_project_dir(project_path) / "latest").write_text(session_id, encoding="utf-8")


def list_recent(project_path: str, max_n: int = 5) -> list[dict]:
    """
    Return metadata for the N most recent sessions for this project.
    Each entry: {session_id, path, turns, last_message, mtime_str}
    """
    d = _project_dir(project_path)
    results = []
    for f in sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        messages = load_messages(f)
        if not messages:
            continue
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if not user_msgs:
            continue
        last = user_msgs[-1].get("content", "")
        last_preview = last[:70].replace("\n", " ") if last else ""
        import datetime
        mtime = f.stat().st_mtime
        dt = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        results.append({
            "session_id":   f.stem,
            "path":         f,
            "turns":        len(user_msgs),
            "last_message": last_preview,
            "mtime":        mtime,
            "mtime_str":    dt,
        })
        if len(results) >= max_n:
            break
    return results
