"""Load lanebridge.conf (INI). Every option has a default; the file is optional."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from typing import List, Optional

DEFAULT_PATH = "~/printer_data/config/lanebridge.conf"


@dataclass
class Config:
    websocket_url: str = "ws://localhost:7125/websocket"
    tools: Optional[List[int]] = None  # None = auto (from [toolchanger])
    object_template: str = "gcode_macro T{tool}"
    variable: str = "spool_id"
    namespace: str = "lane_data"
    archived_as_empty: bool = True
    refresh_interval: float = 300.0
    log_file: str = "~/printer_data/logs/lanebridge.log"
    log_level: str = "INFO"
    reconnect_min: float = 1.0
    reconnect_max: float = 30.0
    debounce: float = 0.25


def _parse_tools(raw: str) -> Optional[List[int]]:
    raw = raw.strip().lower()
    if raw in ("", "auto"):
        return None
    tools = sorted({int(p) for p in raw.replace(" ", "").split(",") if p != ""})
    if any(t < 0 for t in tools):
        raise ValueError("tool numbers must be >= 0")
    return tools


def load(path: Optional[str] = None) -> Config:
    cfg = Config()
    path = os.path.expanduser(path or DEFAULT_PATH)
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cp.read_file(f)

    def get(section: str, key: str, default: str) -> str:
        return cp.get(section, key, fallback=default)

    cfg.websocket_url = get("moonraker", "websocket_url", cfg.websocket_url)
    cfg.tools = _parse_tools(get("tools", "tools", "auto"))
    cfg.object_template = get("tools", "object", cfg.object_template)
    cfg.variable = get("tools", "variable", cfg.variable)
    cfg.namespace = get("lanes", "namespace", cfg.namespace)
    cfg.archived_as_empty = cp.getboolean("lanes", "archived_as_empty", fallback=cfg.archived_as_empty)
    cfg.refresh_interval = cp.getfloat("spoolman", "refresh_interval", fallback=cfg.refresh_interval)
    cfg.log_file = get("logging", "file", cfg.log_file)
    cfg.log_level = get("logging", "level", cfg.log_level).upper()
    if cfg.refresh_interval < 10:
        raise ValueError("[spoolman] refresh_interval must be >= 10 seconds")
    if "{tool}" not in cfg.object_template and "{n}" not in cfg.object_template:
        raise ValueError("[tools] object must contain {tool}")
    return cfg
