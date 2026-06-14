"""
Project symbol index (Feature 5) — scans the project directory at session start
and returns a compact file/symbol map injected into the system prompt so the
agent knows what exists without cold-reading files just to discover structure.
"""
import os
import re
from pathlib import Path

SKIP_DIRS = {
    '__pycache__', 'node_modules', '.git', 'dist', 'build',
    '.venv', 'venv', 'env', 'ENV', 'coverage', '.next',
    'out', 'target', 'vendor', 'bower_components', '.mypy_cache',
    '.pytest_cache', '.tox', 'htmlcov', '.eggs', 'site-packages',
}
SKIP_EXTS = {
    '.pyc', '.pyo', '.pyd', '.so', '.dll', '.exe', '.bin',
    '.lock', '.whl', '.egg', '.png', '.jpg', '.jpeg', '.gif',
    '.ico', '.svg', '.mp4', '.mp3', '.zip', '.tar', '.gz',
    '.map', '.wasm', '.db', '.sqlite', '.sqlite3',
}
CODE_EXTS   = {'.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go',
               '.rs', '.rb', '.php', '.cs', '.cpp', '.c', '.h', '.kt'}
CONFIG_EXTS = {'.json', '.yaml', '.yml', '.toml', '.ini', '.cfg',
               '.conf', '.env', '.md', '.txt', '.sh', '.bat'}


def _extract_symbols(path: Path) -> list[str]:
    """Extract top-level class / function names from a source file."""
    symbols: list[str] = []
    try:
        content = path.read_text(encoding='utf-8', errors='replace')
        ext = path.suffix.lower()

        if ext == '.py':
            for m in re.finditer(r'^(?:async\s+)?def\s+(\w+)', content, re.MULTILINE):
                symbols.append(f'def {m.group(1)}')
            for m in re.finditer(r'^class\s+(\w+)', content, re.MULTILINE):
                symbols.append(f'class {m.group(1)}')

        elif ext in ('.js', '.ts', '.jsx', '.tsx'):
            for m in re.finditer(
                r'^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)',
                content, re.MULTILINE
            ):
                symbols.append(f'fn {m.group(1)}')
            for m in re.finditer(r'^(?:export\s+)?class\s+(\w+)', content, re.MULTILINE):
                symbols.append(f'class {m.group(1)}')
            for m in re.finditer(
                r'^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(',
                content, re.MULTILINE
            ):
                symbols.append(f'const {m.group(1)}')

        elif ext == '.go':
            for m in re.finditer(
                r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)',
                content, re.MULTILINE
            ):
                symbols.append(f'func {m.group(1)}')

        elif ext in ('.java', '.kt'):
            for m in re.finditer(
                r'(?:public|private|protected)\s+(?:static\s+)?(?:\w+\s+)+(\w+)\s*\(',
                content
            ):
                symbols.append(f'fn {m.group(1)}')
            for m in re.finditer(
                r'(?:class|interface|object)\s+(\w+)',
                content
            ):
                symbols.append(f'class {m.group(1)}')

    except Exception:
        pass

    seen: set[str] = set()
    unique: list[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique[:10]


def build_project_index(project_path: str, max_files: int = 150) -> str:
    """
    Walk the project and return a compact symbol map string.
    Injected into the system prompt so the model knows what files/symbols
    exist without having to call list_dir or read files to discover structure.
    Returns empty string for empty or single-file projects.
    """
    import time
    root       = Path(project_path).resolve()
    entries:   list[str] = []
    file_count = 0
    deadline   = time.monotonic() + 3.0  # 3-second hard cap

    for dirpath, dirnames, filenames in os.walk(root):
        if time.monotonic() > deadline:
            entries.append(f"  ... (index timed out — use glob/grep to find files)")
            break

        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith('.')
        )

        for fname in sorted(filenames):
            if file_count >= max_files:
                entries.append(f"  ... (index capped at {max_files} files — use glob/grep)")
                break
            if time.monotonic() > deadline:
                break

            fpath = Path(dirpath) / fname
            ext   = fpath.suffix.lower()

            if ext in SKIP_EXTS or fpath.name.startswith('.'):
                continue
            try:
                if fpath.stat().st_size > 400_000:
                    continue
            except OSError:
                continue

            try:
                rel_path = str(fpath.relative_to(root))
            except ValueError:
                rel_path = str(fpath)

            if ext in CODE_EXTS:
                try:
                    content    = fpath.read_text(encoding='utf-8', errors='replace')
                    line_count = content.count('\n') + 1
                    symbols    = _extract_symbols(fpath)
                    sym_str    = f"  [{', '.join(symbols[:8])}]" if symbols else ""
                    entries.append(f"  {rel_path}  ({line_count}L){sym_str}")
                except Exception:
                    entries.append(f"  {rel_path}")
            elif ext in CONFIG_EXTS:
                entries.append(f"  {rel_path}")

            file_count += 1

        if file_count >= max_files:
            break

    if not entries:
        return ""

    lines = [f"\n\n## Project Index — {root.name}  ({file_count} files)"]
    lines.extend(entries)
    lines.append("\nUse grep to locate a specific symbol before reading its file.")
    return "\n".join(lines)
