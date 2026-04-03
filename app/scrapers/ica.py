# app/scrapers/ica.py
"""Scraper för ICA Handla.

Playwright körs i en separat subprocess (_ica_worker.py) för att kringgå
Python 3.14/Windows begränsningen att asyncio.create_subprocess_exec kräver
ProactorEventLoop — vilket uvicorn inte garanterar.

Kommunikation via stdin/stdout med JSON-protokoll.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ICA_STORE_ZIP = os.getenv("ICA_STORE_ZIP", "17141")
ICA_STORE_ID  = os.getenv("ICA_STORE_ID", "")

_WORKER_SCRIPT = str(Path(__file__).parent / "_ica_worker.py")

_worker_proc: subprocess.Popen | None = None
_worker_ready: bool = False
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _kill_worker() -> None:
    global _worker_proc, _worker_ready
    if _worker_proc and _worker_proc.poll() is None:
        _worker_proc.terminate()
    _worker_proc = None
    _worker_ready = False


atexit.register(_kill_worker)


def _start_worker() -> bool:
    """Starta worker-subprocess och vänta på READY-signal (blockerande)."""
    global _worker_proc, _worker_ready

    _kill_worker()
    try:
        env = os.environ.copy()
        env["ICA_STORE_ZIP"] = ICA_STORE_ZIP
        env["PYTHONIOENCODING"] = "utf-8"
        env["PLAYWRIGHT_BROWSERS_PATH"] = "/app/.playwright"
        if ICA_STORE_ID:
            env["ICA_STORE_ID"] = ICA_STORE_ID

        extra = {}
        if sys.platform == "win32":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW

        _worker_proc = subprocess.Popen(
            [sys.executable, _WORKER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,          # ärv stderr → loggar syns i uvicorn
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
            **extra,
        )

        logger.info("ICA: worker startad, inväntar butiksval (~15 sek)...")
        first_line = _worker_proc.stdout.readline().strip()

        if first_line == "READY":
            _worker_ready = True
            logger.info("ICA: worker redo")
            return True
        else:
            logger.error(f"ICA: worker initialisering misslyckades (svar: {first_line!r})")
            _kill_worker()
            return False

    except Exception as e:
        logger.error(f"ICA: kunde inte starta worker: {e}")
        _kill_worker()
        return False


def _ensure_worker() -> bool:
    if _worker_proc is not None and _worker_proc.poll() is None and _worker_ready:
        return True
    return _start_worker()


async def search_products(query: str, size: int = 30) -> list[dict]:
    """Sök produkter på ICA. Returnerar tom lista om worker inte kan startas."""
    async with _get_lock():
        loop = asyncio.get_event_loop()

        # Starta worker om nödvändigt (blockerande, men bara första gången per session)
        try:
            ready = await asyncio.wait_for(
                loop.run_in_executor(None, _ensure_worker),
                timeout=120.0,
            )
        except asyncio.TimeoutError:
            logger.error("ICA: timeout under worker-initialisering")
            return []

        if not ready:
            return []

        try:
            request = json.dumps({"query": query, "size": size}, ensure_ascii=False) + "\n"
            await loop.run_in_executor(None, _worker_proc.stdin.write, request)
            await loop.run_in_executor(None, _worker_proc.stdin.flush)

            response = await asyncio.wait_for(
                loop.run_in_executor(None, _worker_proc.stdout.readline),
                timeout=90.0,
            )

            if response.strip():
                return json.loads(response.strip())
            return []

        except asyncio.TimeoutError:
            logger.warning(f"ICA: timeout för sökning av '{query}'")
            _kill_worker()
            return []
        except Exception as e:
            logger.error(f"ICA sökfel '{query}': {e}")
            _kill_worker()
            return []
