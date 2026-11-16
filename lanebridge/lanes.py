"""Pure functions that turn a Spoolman spool into a lane_data entry.

OrcaSlicer (MoonrakerPrinterAgent::fetch_moonraker_filament_data) reads every
object in the lane_data namespace and uses:

* ``lane``        - slot index, must be a JSON *string* ("0", "1", ...);
                    a number is silently skipped.
* ``material``    - filament type; an empty string means "no filament".
* ``color``       - "#RRGGBB" / "RRGGBB" / "RRGGBBAA".
* ``nozzle_temp`` / ``bed_temp`` - JSON numbers; null means "not set".

The key name ("lane1", ...) is not used by Orca, but the AFC / Happy Hare
convention is lane<n+1> for tool n, which other plugins rely on.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

LANE_KEY_RE = re.compile(r"^lane(\d+)$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")

LaneEntry = Dict[str, Any]


def lane_key(tool: int) -> str:
    return f"lane{tool + 1}"


def tool_for_key(key: str) -> Optional[int]:
    m = LANE_KEY_RE.match(key)
    if not m or int(m.group(1)) < 1:
        return None
    return int(m.group(1)) - 1


def normalize_color(value: Any) -> str:
    """Spoolman hex ("404040", "#404040", "404040FF") -> "#404040"; invalid -> ""."""
    if not isinstance(value, str):
        return ""
    v = value.strip()
    if v.lower().startswith("0x"):
        v = v[2:]
    v = v.lstrip("#")
    if not _HEX_RE.match(v):
        return ""
    return "#" + v[:6].lower()


def _first_color(filament: Dict[str, Any]) -> str:
    multi = filament.get("multi_color_hexes")
    if isinstance(multi, str) and multi.strip():
        for part in multi.split(","):
            color = normalize_color(part)
            if color:
                return color
    return normalize_color(filament.get("color_hex"))


def _temp(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        t = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return t if t > 0 else None


def empty_lane(tool: int) -> LaneEntry:
    return {
        "color": "",
        "material": "",
        "bed_temp": None,
        "nozzle_temp": None,
        "scan_time": "",
        "td": "",
        "lane": str(tool),
        "spool_id": None,
    }


def build_lane(
    tool: int, spool: Optional[Dict[str, Any]], archived_as_empty: bool = True
) -> LaneEntry:
    """Lane entry for ``tool`` holding ``spool`` (a Spoolman /v1/spool object).

    ``spool`` is None when the tool has no spool or the spool does not exist.
    """
    entry = empty_lane(tool)
    if not isinstance(spool, dict):
        return entry
    if archived_as_empty and spool.get("archived"):
        return entry
    filament = spool.get("filament") or {}
    material = filament.get("material")
    entry["color"] = _first_color(filament)
    entry["material"] = material.strip() if isinstance(material, str) else ""
    entry["nozzle_temp"] = _temp(filament.get("settings_extruder_temp"))
    entry["bed_temp"] = _temp(filament.get("settings_bed_temp"))
    sid = spool.get("id")
    entry["spool_id"] = sid if isinstance(sid, int) and not isinstance(sid, bool) else None
    return entry


def stale_keys(existing: Iterable[str], tools: Iterable[int]) -> list:
    """lane<N> keys that do not belong to any configured tool."""
    wanted = {lane_key(t) for t in tools}
    return sorted(
        (k for k in existing if tool_for_key(k) is not None and k not in wanted),
        key=lambda k: tool_for_key(k),
    )


def describe(entry: LaneEntry) -> str:
    if not entry.get("material") and entry.get("spool_id") is None:
        return "empty"
    parts = [entry.get("material") or "?", entry.get("color") or "no colour"]
    if entry.get("nozzle_temp"):
        parts.append(f"{entry['nozzle_temp']}C")
    parts.append(f"spool {entry.get('spool_id')}")
    return " ".join(parts)
