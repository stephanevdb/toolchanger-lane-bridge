"""Where the spool ID per tool comes from.

Today: a variable on a Klipper object (``gcode_macro T<n>.spool_id``, the
Mainsail/Fluidd convention). A later source (e.g. NFC) only has to provide the
same three methods.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def parse_spool_id(value: Any) -> Optional[int]:
    """Klipper macro values: 9, "9", None, "None", "", 0, -1 -> int or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str):
        value = value.strip().strip("'\"")
        if not value.isdigit():
            return None
        value = int(value)
    if isinstance(value, int) and value > 0:
        return value
    return None


class MacroVariableSource:
    def __init__(self, object_template: str, variable: str) -> None:
        self.object_template = object_template
        self.variable = variable

    def object_name(self, tool: int) -> str:
        return self.object_template.format(tool=tool, n=tool)

    def subscription(self, tools: Iterable[int]) -> Dict[str, List[str]]:
        return {self.object_name(t): [self.variable] for t in tools}

    def extract(self, status: Dict[str, Any], tools: Iterable[int]) -> Dict[int, Optional[int]]:
        """Spool IDs present in a (partial) status update, keyed by tool."""
        out: Dict[int, Optional[int]] = {}
        for t in tools:
            obj = status.get(self.object_name(t))
            if isinstance(obj, dict) and self.variable in obj:
                out[t] = parse_spool_id(obj[self.variable])
        return out
