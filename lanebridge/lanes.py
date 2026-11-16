"""Pure functions that turn a Spoolman spool into a lane_data entry.

OrcaSlicer (MoonrakerPrinterAgent::fetch_moonraker_filament_data) reads every
object in the lane_data namespace and uses:

* ``lane``        - slot index, must be a JSON *string* ("0", "1", ...);
                    a number is silently skipped.
* ``material``    - filament type; an empty string means "no filament".
* ``color``       - "#RRGGBB" / "RRGGBB" / "RRGGBBAA".
* ``nozzle_temp`` / ``bed_temp`` - JSON numbers; null means "not set".
* ``filament_id`` - (string) an *Orca preset* id. Never written here: Spoolman
                    ids are not Orca preset ids, and a wrong one would make Orca
                    pick the wrong preset. Spoolman's ids go in
                    ``spoolman_filament_id`` / ``external_id`` instead.

Everything else is extra Spoolman data for plugins and future readers; Orca
ignores keys it does not know. Every entry has the same set of keys, empty
lanes included.

The key name ("lane1", ...) is not used by Orca, but the AFC / Happy Hare
convention is lane<n+1> for tool n, which other plugins rely on.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

LANE_KEY_RE = re.compile(r"^lane(\d+)$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")

LaneEntry = Dict[str, Any]

# Keys whose change is worth an INFO log line; the rest (weights, last_used, ...)
# change with every print and are logged at DEBUG.
IDENTITY_KEYS = ("spool_id", "material", "color", "nozzle_temp", "bed_temp", "name", "vendor")


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


def _colors(filament: Dict[str, Any]) -> List[str]:
    multi = filament.get("multi_color_hexes")
    if isinstance(multi, str) and multi.strip():
        cols = [c for c in (normalize_color(p) for p in multi.split(",")) if c]
        if cols:
            return cols
    single = normalize_color(filament.get("color_hex"))
    return [single] if single else []


def _temp(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        t = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return t if t > 0 else None


def _num(value: Any, digits: int = 2) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _extra(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Spoolman extra fields are JSON-encoded strings; decode what we can."""
    out: Dict[str, Any] = {}
    extra = obj.get("extra")
    if not isinstance(extra, dict):
        return out
    for k, v in extra.items():
        if isinstance(v, str):
            try:
                out[k] = json.loads(v)
                continue
            except ValueError:
                pass
        out[k] = v
    return out


def empty_lane(tool: int) -> LaneEntry:
    return {
        # read by OrcaSlicer
        "lane": str(tool),
        "material": "",
        "color": "",
        "nozzle_temp": None,
        "bed_temp": None,
        # AFC / Happy Hare compatible
        "spool_id": None,
        "scan_time": "",
        "td": "",
        # extra Spoolman data
        "name": "",
        "vendor": "",
        "colors": [],
        "multi_color_direction": "",
        "spoolman_filament_id": None,
        "external_id": "",
        "diameter": None,
        "density": None,
        "remaining_weight": None,
        "remaining_length": None,
        "remaining_percent": None,
        "used_weight": None,
        "used_length": None,
        "initial_weight": None,
        "spool_weight": None,
        "price": None,
        "location": "",
        "lot_nr": "",
        "comment": "",
        "archived": False,
        "registered": "",
        "first_used": "",
        "last_used": "",
        "spool_extra": {},
        "filament_extra": {},
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
    vendor = filament.get("vendor") or {}
    colors = _colors(filament)
    spool_extra = _extra(spool)
    filament_extra = _extra(filament)

    entry["material"] = _str(filament.get("material"))
    entry["color"] = colors[0] if colors else ""
    entry["nozzle_temp"] = _temp(filament.get("settings_extruder_temp"))
    entry["bed_temp"] = _temp(filament.get("settings_bed_temp"))
    entry["spool_id"] = _int(spool.get("id"))
    td = filament_extra.get("td", spool_extra.get("td"))
    entry["td"] = "" if td is None else str(td)

    entry["name"] = _str(filament.get("name"))
    entry["vendor"] = _str(vendor.get("name")) if isinstance(vendor, dict) else ""
    entry["colors"] = colors
    entry["multi_color_direction"] = _str(filament.get("multi_color_direction")) if len(colors) > 1 else ""
    entry["spoolman_filament_id"] = _int(filament.get("id"))
    entry["external_id"] = _str(filament.get("external_id"))
    entry["diameter"] = _num(filament.get("diameter"))
    entry["density"] = _num(filament.get("density"))

    remaining = _num(spool.get("remaining_weight"), 1)
    initial = _num(spool.get("initial_weight", filament.get("weight")), 1)
    entry["remaining_weight"] = remaining
    entry["remaining_length"] = _num(spool.get("remaining_length"), 0)
    entry["used_weight"] = _num(spool.get("used_weight"), 1)
    entry["used_length"] = _num(spool.get("used_length"), 0)
    entry["initial_weight"] = initial
    entry["spool_weight"] = _num(spool.get("spool_weight", filament.get("spool_weight")), 1)
    if remaining is not None and initial:
        entry["remaining_percent"] = max(0, min(100, round(remaining / initial * 100)))
    entry["price"] = _num(spool.get("price", filament.get("price")))

    entry["location"] = _str(spool.get("location"))
    entry["lot_nr"] = _str(spool.get("lot_nr"))
    entry["comment"] = _str(spool.get("comment"))
    entry["archived"] = bool(spool.get("archived"))
    entry["registered"] = _str(spool.get("registered"))
    entry["first_used"] = _str(spool.get("first_used"))
    entry["last_used"] = _str(spool.get("last_used"))
    entry["spool_extra"] = spool_extra
    entry["filament_extra"] = filament_extra
    return entry


def identity_changed(old: Optional[LaneEntry], new: LaneEntry) -> bool:
    if not isinstance(old, dict):
        return True
    return any(old.get(k) != new.get(k) for k in IDENTITY_KEYS)


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
    if entry.get("remaining_weight") is not None:
        parts.append(f"{entry['remaining_weight']:g}g left")
    parts.append(f"spool {entry.get('spool_id')}")
    return " ".join(parts)
