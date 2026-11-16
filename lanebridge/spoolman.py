"""Spool lookup through Moonraker's Spoolman proxy, with a TTL cache."""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

Spool = Dict[str, Any]
Fetch = Callable[[int], Awaitable[Optional[Spool]]]


class SpoolmanUnavailable(Exception):
    """Spoolman could not answer (down, timeout, 5xx). Keep the old lane."""


def make_moonraker_fetch(call: Callable[..., Awaitable[Any]]) -> Fetch:
    """Build a fetch function on top of a JSON-RPC ``call(method, params)``."""

    async def fetch(spool_id: int) -> Optional[Spool]:
        res = await call(
            "server.spoolman.proxy",
            {
                "request_method": "GET",
                "path": f"/v1/spool/{int(spool_id)}",
                "use_v2_response": True,
            },
        )
        err = (res or {}).get("error")
        if err:
            if err.get("status_code") == 404:
                return None
            raise SpoolmanUnavailable(
                f"{err.get('status_code')}: {err.get('message')}"
            )
        spool = res.get("response")
        if not isinstance(spool, dict):
            raise SpoolmanUnavailable("unexpected proxy response")
        return spool

    return fetch


class SpoolCache:
    """Caches spool lookups (including "not found") for ``ttl`` seconds."""

    def __init__(
        self, fetch: Fetch, ttl: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl
        self._clock = clock
        self._entries: Dict[int, Tuple[float, Optional[Spool]]] = {}

    async def get(self, spool_id: int, force: bool = False) -> Optional[Spool]:
        now = self._clock()
        hit = self._entries.get(spool_id)
        if hit is not None and not force and now - hit[0] < self._ttl:
            return hit[1]
        spool = await self._fetch(spool_id)
        self._entries[spool_id] = (now, spool)
        return spool

    def invalidate(self, spool_id: Optional[int] = None) -> None:
        if spool_id is None:
            self._entries.clear()
        else:
            self._entries.pop(spool_id, None)
