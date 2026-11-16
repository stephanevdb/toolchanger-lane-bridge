"""Minimal Moonraker JSON-RPC client over the websocket."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Callable, Dict, Optional

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from . import __version__

log = logging.getLogger(__name__)

Notify = Callable[[str, list], None]


class MoonrakerError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ConnectionLost(Exception):
    pass


class MoonrakerClient:
    def __init__(self, url: str, on_notify: Optional[Notify] = None) -> None:
        self.url = url
        self.on_notify = on_notify
        self._ws = None
        self._ids = itertools.count(1)
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader: Optional[asyncio.Task] = None
        self.closed = asyncio.Event()

    async def connect(self, timeout: float = 10.0) -> None:
        self._ws = await asyncio.wait_for(
            connect(self.url, max_size=None, ping_interval=20, ping_timeout=20,
                    open_timeout=timeout),
            timeout + 1,
        )
        self.closed.clear()
        self._reader = asyncio.create_task(self._read_loop())

    async def identify(self) -> Any:
        return await self.call(
            "server.connection.identify",
            {"client_name": "lanebridge", "version": __version__,
             "type": "other", "url": "local"},
        )

    async def call(self, method: str, params: Optional[dict] = None,
                   timeout: float = 15.0) -> Any:
        if self._ws is None or self.closed.is_set():
            raise ConnectionLost("not connected")
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": rid}
        if params is not None:
            msg["params"] = params
        try:
            await self._ws.send(json.dumps(msg))
            return await asyncio.wait_for(fut, timeout)
        except ConnectionClosed as e:
            raise ConnectionLost(str(e)) from e
        finally:
            self._pending.pop(rid, None)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                rid = msg.get("id")
                if rid is not None and rid in self._pending:
                    fut = self._pending[rid]
                    if fut.done():
                        continue
                    if "error" in msg:
                        err = msg["error"] or {}
                        fut.set_exception(MoonrakerError(err.get("code", -1), str(err.get("message"))))
                    else:
                        fut.set_result(msg.get("result"))
                elif "method" in msg and self.on_notify is not None:
                    try:
                        self.on_notify(msg["method"], msg.get("params") or [])
                    except Exception:
                        log.exception("notification handler failed for %s", msg["method"])
        except ConnectionClosed:
            pass
        except Exception:
            log.exception("websocket reader crashed")
        finally:
            self.closed.set()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionLost("connection closed"))

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._reader.cancel()
        self.closed.set()
