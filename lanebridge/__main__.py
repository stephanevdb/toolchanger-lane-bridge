"""Entry point: python -m lanebridge [-c lanebridge.conf] [--once] [--dry-run] [--purge]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys

from . import __version__
from .bridge import Bridge
from .config import DEFAULT_PATH, Config, load


class _JournalFormatter(logging.Formatter):
    PRIORITY = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4,
                logging.ERROR: 3, logging.CRITICAL: 2}

    def format(self, record: logging.LogRecord) -> str:
        return f"<{self.PRIORITY.get(record.levelno, 6)}>{super().format(record)}"


def setup_logging(cfg: Config, verbose: bool, to_file: bool) -> None:
    level = logging.DEBUG if verbose else getattr(logging, cfg.log_level, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    stream = logging.StreamHandler(sys.stderr)
    if os.environ.get("JOURNAL_STREAM"):
        # journald adds its own timestamp; a "<N>" prefix sets the syslog
        # priority so `journalctl -p warning` works.
        stream.setFormatter(_JournalFormatter("%(levelname)s %(message)s"))
    else:
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    root.addHandler(stream)
    path = os.path.expanduser(cfg.log_file) if cfg.log_file else ""
    if to_file and path and os.path.isdir(os.path.dirname(path)):
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(fh)
    # websockets logs every handshake failure at INFO/ERROR; keep it quiet.
    logging.getLogger("websockets").setLevel(logging.WARNING)


async def _main(args: argparse.Namespace, cfg: Config) -> int:
    bridge = Bridge(cfg, dry_run=args.dry_run)
    if args.purge:
        keys = await bridge.purge()
        print(f"removed: {', '.join(keys) or 'nothing'}")
        return 0
    if args.once or args.dry_run:
        await bridge.run_once()
        return 0

    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    logging.getLogger("lanebridge").info("lanebridge %s starting", __version__)
    try:
        await bridge.run_forever()
    except asyncio.CancelledError:
        logging.getLogger("lanebridge").info("stopping")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="lanebridge", description=__doc__)
    p.add_argument("-c", "--config", default=DEFAULT_PATH, help="path to lanebridge.conf")
    p.add_argument("--once", action="store_true", help="sync once and exit")
    p.add_argument("--dry-run", action="store_true", help="print the entries instead of writing (implies --once)")
    p.add_argument("--purge", action="store_true", help="delete all laneN keys from lane_data and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args(argv)
    cfg = load(args.config)
    setup_logging(cfg, args.verbose, to_file=not args.dry_run)
    try:
        return asyncio.run(_main(args, cfg))
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # --once / --purge failures
        logging.getLogger("lanebridge").error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
