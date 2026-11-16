"""Keeps Moonraker's lane_data in step with the spool assigned to each tool."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Callable, Dict, Iterable, List, Optional, Set

from websockets.exceptions import WebSocketException

from .config import Config
from .lanes import build_lane, describe, identity_changed, lane_key, stale_keys, tool_for_key
from .moonraker import ConnectionLost, MoonrakerClient, MoonrakerError
from .sources import MacroVariableSource
from .spoolman import SpoolCache, SpoolmanUnavailable, make_moonraker_fetch

log = logging.getLogger("lanebridge")

RETRYABLE = (OSError, ConnectionLost, WebSocketException, asyncio.TimeoutError, MoonrakerError)


class Bridge:
    def __init__(
        self,
        cfg: Config,
        client_factory: Callable[..., MoonrakerClient] = MoonrakerClient,
        dry_run: bool = False,
        out: Callable[[str], None] = print,
    ) -> None:
        self.cfg = cfg
        self.client_factory = client_factory
        self.dry_run = dry_run
        self.out = out
        self.source = MacroVariableSource(cfg.object_template, cfg.variable)
        self.cache = SpoolCache(make_moonraker_fetch(self._call), cfg.refresh_interval)
        self.client: Optional[MoonrakerClient] = None
        self.tools: List[int] = []
        self.spool_ids: Dict[int, Optional[int]] = {}
        self.written: Dict[str, dict] = {}  # what lane_data holds, as far as we know
        self._lock = asyncio.Lock()
        self._klippy_lost = asyncio.Event()
        self._pending: Set[int] = set()
        self._pending_force: Set[int] = set()
        self._debounce_task: Optional[asyncio.Task] = None
        self._warned: Set[str] = set()
        self._temps: Dict[int, int] = {}  # what we last set in Klipper's temp macro
        self._temps_off = False
        self.session_established = False

    # ------------------------------------------------------------------ rpc
    async def _call(self, method: str, params: Optional[dict] = None):
        if self.client is None:
            raise ConnectionLost("not connected")
        return await self.client.call(method, params)

    def _warn_once(self, tag: str, msg: str, *args) -> None:
        if tag not in self._warned:
            self._warned.add(tag)
            log.warning(msg, *args)

    # -------------------------------------------------------- notifications
    def _on_notify(self, method: str, params: list) -> None:
        if method == "notify_status_update" and params:
            changes = self.source.extract(params[0], self.tools)
            changed = [t for t, sid in changes.items() if self.spool_ids.get(t, -1) != sid]
            for t in changed:
                log.debug("T%d spool_id -> %s", t, changes[t])
                self.spool_ids[t] = changes[t]
            if changed:
                # Fresh Spoolman data when someone picks a spool.
                self._schedule(changed, force=True)
        elif method in ("notify_klippy_disconnected", "notify_klippy_shutdown"):
            self._klippy_lost.set()
        elif method == "notify_spoolman_status_changed":
            if params and isinstance(params[0], dict) and params[0].get("spoolman_connected"):
                self._schedule(self.tools, force=True)

    def _schedule(self, tools: Iterable[int], force: bool = False) -> None:
        tools = list(tools)
        self._pending.update(tools)
        if force:
            self._pending_force.update(tools)
        if self._debounce_task is None or self._debounce_task.done():
            self._debounce_task = asyncio.create_task(self._debounced())

    async def _debounced(self) -> None:
        await asyncio.sleep(self.cfg.debounce)
        while self._pending:
            tools, force = set(self._pending), set(self._pending_force)
            self._pending.clear()
            self._pending_force.clear()
            try:
                await self.sync(tools & force, force=True)
                await self.sync(tools - force)
            except RETRYABLE as e:
                log.debug("sync interrupted: %s", e)
                return

    # ----------------------------------------------------------------- sync
    async def sync(self, tools: Iterable[int], force: bool = False) -> None:
        tools = sorted(set(tools))
        if not tools:
            return
        async with self._lock:
            for t in tools:
                sid = self.spool_ids.get(t)
                spool = None
                if sid is not None:
                    try:
                        spool = await self.cache.get(sid, force=force)
                    except (SpoolmanUnavailable, MoonrakerError) as e:
                        self._warn_once("spoolman", "Spoolman unavailable (%s); keeping current lanes", e)
                        continue
                    self._warned.discard("spoolman")
                    if spool is None:
                        self._warn_once(f"missing{sid}", "T%d: spool %d not found in Spoolman", t, sid)
                    else:
                        self._warned.discard(f"missing{sid}")
                entry = build_lane(t, spool, self.cfg.archived_as_empty)
                await self._write(t, entry)
                await self._push_temp(t, entry)

    async def _write(self, tool: int, entry: dict) -> bool:
        key = lane_key(tool)
        same = self.written.get(key) == entry
        if self.dry_run:
            self.out(f"{key} ({'unchanged' if same else 'would write'}): {json.dumps(entry)}")
            return False
        if same:
            return False
        old = self.written.get(key)
        await self._call(
            "server.database.post_item",
            {"namespace": self.cfg.namespace, "key": key, "value": entry},
        )
        self.written[key] = entry
        # Weights and dates change with every print; only log those at DEBUG.
        (log.info if identity_changed(old, entry) else log.debug)(
            "T%d -> %s: %s", tool, key, describe(entry))
        return True

    async def _push_temp(self, tool: int, entry: dict) -> None:
        """Mirror the spool's nozzle temperature into a Klipper macro variable (t<tool>).

        Only sent when the value changes, so prints don't get extra G-code.
        """
        macro = self.cfg.temp_macro
        if not macro or self._temps_off:
            return
        temp = entry.get("nozzle_temp")
        value = int(round(temp)) if isinstance(temp, (int, float)) and temp > 0 else 0
        if self._temps.get(tool) == value:
            return
        script = f"SET_GCODE_VARIABLE MACRO={macro} VARIABLE=t{tool} VALUE={value}"
        if self.dry_run:
            self.out(f"T{tool} temp (would run): {script}")
            return
        try:
            await self._call("printer.gcode.script", {"script": script})
        except MoonrakerError as e:
            # Usually: the macro isn't installed. Lanes still work; just stop trying.
            self._temps_off = True
            self._warn_once("tempmacro", "can't set %s.t%d (%s); add klipper/lanebridge_temps.cfg "
                            "or set [klipper] temp_macro to empty", macro, tool, e.message)
            return
        self._temps[tool] = value
        log.debug("T%d nozzle temp -> %d", tool, value)

    async def load_existing(self) -> Dict[str, dict]:
        try:
            res = await self._call("server.database.get_item", {"namespace": self.cfg.namespace})
            value = (res or {}).get("value") or {}
        except MoonrakerError as e:
            # Over JSON-RPC Moonraker reports a missing namespace as -32601
            # "Namespace lane_data not found" (404 over HTTP).
            if e.code != 404 and "not found" not in e.message.lower():
                raise
            value = {}
        self.written = dict(value) if isinstance(value, dict) else {}
        return self.written

    async def delete_keys(self, keys: Iterable[str]) -> None:
        for key in keys:
            if self.dry_run:
                self.out(f"{key} (would delete)")
                continue
            await self._call("server.database.delete_item",
                             {"namespace": self.cfg.namespace, "key": key})
            self.written.pop(key, None)
            log.info("removed %s", key)

    # ---------------------------------------------------------------- setup
    async def wait_klippy_ready(self, timeout: Optional[float] = None) -> None:
        start = time.monotonic()
        waiting_logged = False
        while True:
            info = await self._call("server.info")
            if info.get("klippy_state") == "ready":
                if waiting_logged:
                    log.info("Klipper ready")
                return
            if not waiting_logged:
                log.info("waiting for Klipper (state: %s)", info.get("klippy_state"))
                waiting_logged = True
            if timeout is not None and time.monotonic() - start > timeout:
                raise asyncio.TimeoutError("Klipper not ready")
            await asyncio.sleep(2)

    async def resolve_tools(self) -> List[int]:
        if self.cfg.tools:
            return list(self.cfg.tools)
        res = await self._call("printer.objects.query", {"objects": {"toolchanger": ["tool_numbers"]}})
        nums = ((res or {}).get("status", {}).get("toolchanger") or {}).get("tool_numbers")
        if isinstance(nums, list) and nums:
            return sorted(int(n) for n in nums)
        # No [toolchanger]: fall back to the objects that match the template.
        res = await self._call("printer.objects.list")
        pattern = re.escape(self.cfg.object_template)
        pattern = pattern.replace(re.escape("{tool}"), r"(\d+)").replace(re.escape("{n}"), r"(\d+)")
        rx = re.compile(f"^{pattern}$")
        found = sorted({int(m.group(1)) for o in (res or {}).get("objects", []) if (m := rx.match(o))})
        if not found:
            raise MoonrakerError(-1, "no tools found; set [tools] tools in lanebridge.conf")
        return found

    async def setup_and_sync(self, klippy_timeout: Optional[float] = None) -> None:
        self._klippy_lost.clear()
        await self.wait_klippy_ready(klippy_timeout)
        tools = await self.resolve_tools()
        if tools != self.tools:
            log.info("tools: %s", ", ".join(f"T{t}" for t in tools))
        self.tools = tools
        res = await self._call("printer.objects.subscribe",
                               {"objects": self.source.subscription(tools)})
        self.spool_ids = {t: None for t in tools}
        self.spool_ids.update(self.source.extract((res or {}).get("status", {}), tools))
        await self.load_existing()
        await self.delete_keys(stale_keys(self.written, tools))
        # Klipper forgets macro variables on restart: send every tool's temp again.
        self._temps = {}
        self._temps_off = False
        await self.sync(tools, force=True)

    # ------------------------------------------------------------- lifetime
    async def _connect(self) -> None:
        self.client = self.client_factory(self.cfg.websocket_url, self._on_notify)
        await self.client.connect()
        await self.client.identify()

    async def _disconnect(self) -> None:
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            self._debounce_task = None
        self._pending.clear()
        self._pending_force.clear()
        if self.client is not None:
            await self.client.close()
            self.client = None

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.refresh_interval)
            self._schedule(self.tools, force=True)

    async def session(self) -> None:
        self.session_established = False
        await self._connect()
        self.session_established = True
        log.info("connected to Moonraker")
        refresh = asyncio.create_task(self._refresh_loop())
        try:
            while True:
                await self.setup_and_sync()
                closed = asyncio.create_task(self.client.closed.wait())
                lost = asyncio.create_task(self._klippy_lost.wait())
                await asyncio.wait({closed, lost}, return_when=asyncio.FIRST_COMPLETED)
                closed.cancel()
                lost.cancel()
                if self.client.closed.is_set():
                    raise ConnectionLost("Moonraker connection closed")
                log.info("Klipper disconnected; resyncing when it is back")
        finally:
            refresh.cancel()
            await self._disconnect()

    async def run_forever(self) -> None:
        delay = self.cfg.reconnect_min
        failing = False
        while True:
            started = time.monotonic()
            try:
                await self.session()
            except RETRYABLE as e:
                if self.session_established:
                    # A working connection dropped: warn again, even after earlier failures.
                    failing = False
                if time.monotonic() - started > 60:
                    delay = self.cfg.reconnect_min
                (log.debug if failing else log.warning)(
                    "Moonraker unavailable (%s); retrying in %.0fs", e or type(e).__name__, delay)
                failing = True
            else:
                failing = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.cfg.reconnect_max)

    async def run_once(self) -> None:
        await self._connect()
        try:
            await self.setup_and_sync(klippy_timeout=30)
        finally:
            await self._disconnect()

    async def purge(self) -> List[str]:
        """Delete every lane<N> key (uninstall)."""
        await self._connect()
        try:
            existing = await self.load_existing()
            keys = sorted((k for k in existing if tool_for_key(k) is not None),
                          key=lambda k: tool_for_key(k))
            await self.delete_keys(keys)
            return keys
        finally:
            await self._disconnect()
