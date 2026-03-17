"""
ReasoningLog — captures the agent's chain-of-thought at every step.
Produces a formatted mission log that satisfies the CoT requirement.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class ReasoningEntry:
    phase:      str
    reasoning:  str
    tool_name:  str
    tool_args:  dict
    result:     dict
    next_step:  str
    timestamp:  str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%H:%M:%S"))

    def render(self) -> str:
        args_str   = ", ".join(f"{k}={v!r}" for k, v in self.tool_args.items())
        result_str = _summarise(self.result)
        return (
            f"┌─────────────────────────────────────────────────────────────┐\n"
            f"│ PHASE: {self.phase:<53}│\n"
            f"│ Time:  {self.timestamp:<53}│\n"
            f"├─────────────────────────────────────────────────────────────┤\n"
            f"│ REASONING                                                   │\n"
            f"{_wrap(self.reasoning)}"
            f"│ TOOL CALL: {self.tool_name}({args_str})\n"
            f"│ RESULT:    {result_str}\n"
            f"│ NEXT:      {self.next_step}\n"
            f"└─────────────────────────────────────────────────────────────┘"
        )


class ReasoningLog:
    def __init__(self):
        self.entries: list[ReasoningEntry] = []
        self.start_time = datetime.now(timezone.utc)

    def add(
        self,
        phase:     str,
        reasoning: str,
        tool_name: str,
        tool_args: dict,
        result:    dict,
        next_step: str,
    ) -> None:
        entry = ReasoningEntry(
            phase=phase,
            reasoning=reasoning,
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
            next_step=next_step,
        )
        self.entries.append(entry)

    def render_full(self) -> str:
        elapsed = (datetime.now(timezone.utc) - self.start_time).seconds
        header  = (
            "═" * 65 + "\n"
            "  SIREN — Mission Debrief\n"
            f"  Duration: {elapsed}s   Steps: {len(self.entries)}\n"
            + "═" * 65
        )
        body = "\n\n".join(e.render() for e in self.entries)
        return header + "\n\n" + body

    def last_result(self) -> Optional[dict]:
        return self.entries[-1].result if self.entries else None


# ── helpers ───────────────────────────────────────────────────────────────────

def _summarise(result: dict, max_len: int = 120) -> str:
    import json
    s = json.dumps(result, separators=(",", ":"))
    return s[:max_len] + "…" if len(s) > max_len else s


def _wrap(text: str, width: int = 61) -> str:
    lines = []
    for word in text.split():
        if not lines or len(lines[-1]) + len(word) + 1 > width:
            lines.append(word)
        else:
            lines[-1] += " " + word
    return "".join(f"│   {ln}\n" for ln in lines)