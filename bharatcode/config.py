"""
Config system — reads BHARATCODE.md from project root (like CLAUDE.md).
Also manages API keys and user settings.
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR  = Path.home() / ".bharatcode"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE    = CONFIG_DIR / "tool_log.txt"

# Model aliases — old names redirect to new ones transparently
MODEL_ALIASES = {
    "deepseek-chat":     "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}

DEFAULTS = {
    "api_key":        "",
    "model":          "deepseek-v4-flash",
    "max_iterations": 100,
    "show_tool_calls": True,
    "auto_approve":   False,
    "auto_checkpoint": True,   # git-commit changes after each successful turn
    "theme":          "dark",
}

def load_config() -> dict:
    CONFIG_DIR.mkdir(exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    env_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("BHARATCODE_API_KEY")
    if env_key:
        cfg["api_key"] = env_key
    # Silently migrate old model names to new ones
    cfg["model"] = MODEL_ALIASES.get(cfg.get("model", ""), cfg.get("model", DEFAULTS["model"]))
    return cfg

def save_config(cfg: dict):
    CONFIG_DIR.mkdir(exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get_api_key() -> str:
    key = load_config().get("api_key") or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError(
            "No API key found.\n"
            "Run: bharatcode config --key YOUR_DEEPSEEK_KEY\n"
            "Or set: DEEPSEEK_API_KEY=... in your environment"
        )
    return key

def load_project_instructions(cwd: str = ".") -> str:
    """Read BHARATCODE.md from project root — injected into every system prompt."""
    for name in ["BHARATCODE.md", ".bharatcode.md"]:
        p = Path(cwd) / name
        if p.exists():
            return f"\n\n--- Project Instructions (from {name}) ---\n{p.read_text()}"
    return ""
