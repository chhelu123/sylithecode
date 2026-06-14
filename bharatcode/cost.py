"""
Session cost tracker — inspired by Claude Code's /cost command.
Tracks tokens and calculates DeepSeek API cost per session.
"""
from dataclasses import dataclass, field

# DeepSeek pricing (USD per 1M tokens) — updated for v4 models
PRICING = {
    "deepseek-v4-flash": {
        "input":        0.27,
        "input_cache":  0.07,
        "output":       1.10,
    },
    "deepseek-v4-pro": {
        "input":        0.55,
        "input_cache":  0.14,
        "output":       2.19,
    },
}
# Keep old names working for anyone who has them cached in session_cost.model
PRICING["deepseek-chat"]     = PRICING["deepseek-v4-flash"]
PRICING["deepseek-reasoner"] = PRICING["deepseek-v4-pro"]

_DEFAULT_MODEL = "deepseek-v4-flash"

@dataclass
class SessionCost:
    model:         str   = "deepseek-v4-flash"
    prompt_tokens: int   = 0
    output_tokens: int   = 0
    turns:         int   = 0
    tool_calls:    int   = 0

    def add(self, prompt: int, output: int):
        self.prompt_tokens += prompt
        self.output_tokens += output
        self.turns        += 1

    def add_tool(self):
        self.tool_calls += 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        p = PRICING.get(self.model, PRICING[_DEFAULT_MODEL])
        in_cost  = (self.prompt_tokens / 1_000_000) * p["input"]
        out_cost = (self.output_tokens / 1_000_000) * p["output"]
        return in_cost + out_cost

    def display(self, console):
        from rich.table import Table
        from rich import box

        price = PRICING.get(self.model, PRICING[_DEFAULT_MODEL])
        t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        t.add_column("key",   style="dim",   no_wrap=True)
        t.add_column("value", style="cyan",  no_wrap=True)

        t.add_row("Model",          self.model)
        t.add_row("Turns",          str(self.turns))
        t.add_row("Tool calls",     str(self.tool_calls))
        t.add_row("Input tokens",   f"{self.prompt_tokens:,}")
        t.add_row("Output tokens",  f"{self.output_tokens:,}")
        t.add_row("Total tokens",   f"{self.total_tokens:,}")
        t.add_row("Est. cost",      f"${self.cost_usd:.4f} USD")
        t.add_row("Input price",    f"${price['input']}/1M tokens")
        t.add_row("Output price",   f"${price['output']}/1M tokens")

        console.print("\n[bold]Session Cost[/bold]")
        console.print(t)

# Global session tracker
session_cost = SessionCost()
