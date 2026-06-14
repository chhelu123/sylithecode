"""
Hook system — inspired by Claude Code's PreToolUse / PostToolUse hooks.
Hooks run before/after every tool call and can block, modify, or log.
"""
from typing import Callable, Optional
from dataclasses import dataclass, field

@dataclass
class ToolCall:
    name: str
    args: dict

@dataclass
class HookResult:
    proceed: bool = True          # False = block the tool call
    modified_args: dict = None    # Optionally change the args
    message: str = ""             # Message to show user if blocked

PreToolHook  = Callable[[ToolCall], HookResult]
PostToolHook = Callable[[ToolCall, str], None]   # (tool_call, result)

class HookRegistry:
    def __init__(self):
        self._pre:  list[PreToolHook]  = []
        self._post: list[PostToolHook] = []

    def add_pre(self, hook: PreToolHook):
        self._pre.append(hook)

    def add_post(self, hook: PostToolHook):
        self._post.append(hook)

    def run_pre(self, call: ToolCall) -> HookResult:
        for hook in self._pre:
            result = hook(call)
            if not result.proceed:
                return result
            if result.modified_args:
                call.args = result.modified_args
        return HookResult(proceed=True)

    def run_post(self, call: ToolCall, result: str):
        for hook in self._post:
            hook(call, result)

# Global registry
hooks = HookRegistry()

# ── Built-in Hooks ────────────────────────────────────────────────────────────

def _safety_hook(call: ToolCall) -> HookResult:
    """Block dangerous bash commands."""
    if call.name == "bash":
        cmd = call.args.get("command", "")
        dangerous = ["rm -rf /", "format c:", "DROP TABLE", "sudo rm -rf"]
        for d in dangerous:
            if d in cmd:
                return HookResult(proceed=False, message=f"Blocked dangerous command: {d}")
    return HookResult(proceed=True)

def _log_hook(call: ToolCall) -> HookResult:
    """Log all tool calls to ~/.bharatcode/tool_log.txt"""
    import datetime
    from pathlib import Path
    log_file = Path.home() / ".bharatcode" / "tool_log.txt"
    log_file.parent.mkdir(exist_ok=True)
    with open(log_file, "a") as f:
        ts = datetime.datetime.now().isoformat()
        first_arg = list(call.args.values())[0] if call.args else ""
        f.write(f"{ts} {call.name}({str(first_arg)[:60]})\n")
    return HookResult(proceed=True)

# Register built-in hooks
hooks.add_pre(_safety_hook)
hooks.add_pre(_log_hook)
