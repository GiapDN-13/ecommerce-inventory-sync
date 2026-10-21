"""Small HTTP service so schedulers like n8n can trigger a run: `POST /run?apply=false`."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .config import Settings
from .http import ApiError
from .pipeline import run_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
app = FastAPI(title="inventory-sync")
_running = threading.Lock()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/run")
def run(apply: bool = False) -> dict:
    if not _running.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a sync run is already in progress")
    try:
        settings = Settings.from_env()
        result, _ = run_sync(settings, apply=apply, reports_dir=Path(settings.reports_dir))
        return result
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=f"sync aborted, nothing was changed: {exc}") from exc
    finally:
        _running.release()
