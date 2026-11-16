"""A tiny fake Moonraker websocket server for tests."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any, Dict, List, Optional

from websockets.asyncio.server import serve


class FakeMoonraker:
    def __init__(self, tools=(0, 1), spools: Optional[Dict[int, dict]] = None) -> None:
        self.tools = list(tools)
        self.spools: Dict[int, dict] = spools or {}
        self.spool_ids: Dict[int, Any] = {t: None for t in self.tools}
        self.db: Dict[str, Dict[str, Any]] = {}  # survives restarts, like the real one
        self.klippy_state = "ready"
        self.spoolman_up = True
        self.temp_macro_installed = True
        self.macro_vars: Dict[str, Any] = {}
        self.calls: List[tuple] = []
        self.port: Optional[int] = None
        self._server = None
        self._conns: set = set()

    # -------------------------------------------------------------- control
    async def start(self) -> None:
        self._server = await serve(self._handler, "127.0.0.1", self.port or 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for ws in list(self._conns):
            await ws.close()
        self._server.close()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/websocket"

    def writes(self, key: Optional[str] = None) -> List[dict]:
        return [p for m, p in self.calls if m == "server.database.post_item"
                and (key is None or p["key"] == key)]

    def count(self, method: str) -> int:
        return sum(1 for m, _ in self.calls if m == method)

    async def notify(self, method: str, params: list) -> None:
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        for ws in list(self._conns):
            await ws.send(msg)

    async def set_spool(self, tool: int, spool_id: Any) -> None:
        self.spool_ids[tool] = spool_id
        await self.notify("notify_status_update",
                          [{f"gcode_macro T{tool}": {"spool_id": spool_id}}, 1.0])

    # -------------------------------------------------------------- server
    async def _handler(self, ws) -> None:
        self._conns.add(ws)
        try:
            async for raw in ws:
                msg = json.loads(raw)
                method, params = msg["method"], msg.get("params") or {}
                self.calls.append((method, copy.deepcopy(params)))
                try:
                    result = self._dispatch(method, params)
                    reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
                except KeyError as e:
                    # real Moonraker uses -32601 for "Namespace ... not found"
                    reply = {"jsonrpc": "2.0", "id": msg["id"],
                             "error": {"code": -32601, "message": str(e.args[0])}}
                await ws.send(json.dumps(reply))
        except Exception:
            pass
        finally:
            self._conns.discard(ws)

    def _status(self, objects: Dict[str, Any]) -> Dict[str, Any]:
        status: Dict[str, Any] = {}
        for name in objects:
            if name == "toolchanger":
                status[name] = {"tool_numbers": self.tools}
            elif name.startswith("gcode_macro T"):
                t = int(name.split("T")[-1])
                if t in self.tools:
                    status[name] = {"spool_id": self.spool_ids.get(t)}
        return status

    def _dispatch(self, method: str, p: Dict[str, Any]) -> Any:
        if method == "server.connection.identify":
            return {"connection_id": 1}
        if method == "server.info":
            return {"klippy_state": self.klippy_state}
        if method in ("printer.objects.query", "printer.objects.subscribe"):
            return {"eventtime": 1.0, "status": self._status(p["objects"])}
        if method == "printer.objects.list":
            return {"objects": [f"gcode_macro T{t}" for t in self.tools]}
        if method == "server.database.get_item":
            ns = self.db.get(p["namespace"])
            if ns is None:
                raise KeyError(f"Namespace {p['namespace']} not found")
            if p.get("key") is None:
                return {"namespace": p["namespace"], "key": None, "value": copy.deepcopy(ns)}
            return {"namespace": p["namespace"], "key": p["key"], "value": ns[p["key"]]}
        if method == "server.database.post_item":
            self.db.setdefault(p["namespace"], {})[p["key"]] = copy.deepcopy(p["value"])
            return {"namespace": p["namespace"], "key": p["key"], "value": p["value"]}
        if method == "server.database.delete_item":
            val = self.db.get(p["namespace"], {}).pop(p["key"])
            return {"namespace": p["namespace"], "key": p["key"], "value": val}
        if method == "printer.gcode.script":
            parts = dict(w.split("=", 1) for w in p["script"].split()[1:])
            if not self.temp_macro_installed:
                raise KeyError("Unknown gcode_macro '%s'" % parts["MACRO"].lower())
            self.macro_vars[parts["VARIABLE"]] = int(parts["VALUE"])
            return "ok"
        if method == "server.spoolman.proxy":
            if not self.spoolman_up:
                return {"response": None, "error": {"status_code": 503, "message": "down"}}
            sid = int(p["path"].rsplit("/", 1)[1])
            if sid not in self.spools:
                return {"response": None, "error": {"status_code": 404, "message": "Not Found"}}
            return {"response": copy.deepcopy(self.spools[sid]), "error": None}
        raise KeyError(f"unknown method {method}")


async def wait_for(cond, timeout: float = 3.0, interval: float = 0.02) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(interval)
