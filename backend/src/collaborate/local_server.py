"""Local dev server — a FastAPI shell around the portable turn generator.

This file is the throwaway. Everything it calls (`turn.run`, `prompts`,
`svgdoc`, `claude`) is meant to survive the move to a Worker; this just adapts
that generator to uvicorn instead of `fetch`. It also serves the frontend from
the same origin, which is both how Cloudflare will serve it (Worker + static
assets) and the reason there's no CORS configuration anywhere.

    make dev      # or: uvicorn collaborate.local_server:app --reload
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import httpx

from . import claude, prompts, turn
from .config import ANTHROPIC_MODELS_URL, Settings
from .prompts import REPO_ROOT

FRONTEND_DIR = REPO_ROOT / "frontend"
SAMPLES_DIR = REPO_ROOT / "samples"

app = FastAPI(title="collaborate (dev)")


class TurnRequest(BaseModel):
    svg: str = Field(min_length=1)
    prompt_id: str | None = None
    version: int | None = None


# An API error message can quote the offending credential back at us, so
# nothing from an upstream body reaches a client without passing through here.
_KEY_SHAPED = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")


def _scrub(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return _KEY_SHAPED.sub("[redacted]", text)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


@app.get("/api/health")
async def health() -> dict:
    """Is this server configured *and* able to authenticate?

    The probe is a GET to the models endpoint, which is not billed, so this
    stays free to poll. Like `claude._post_sse` it is local-only: the Worker
    needs its own `fetch` version.
    """
    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        return {"ok": False, "message": str(exc)}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                ANTHROPIC_MODELS_URL, headers=claude.auth_headers(settings)
            )
    except httpx.HTTPError as exc:
        # Offline, DNS, TLS - the key may be fine, so don't blame it.
        return {"ok": False, "auth": f"could not reach the API: {type(exc).__name__}"}

    if response.status_code != 200:
        try:
            detail = (response.json().get("error") or {}).get("message", "")
        except ValueError:
            detail = ""
        detail = _scrub(detail or response.text[:200], settings.api_key)
        return {"ok": False, "auth": f"{response.status_code}: {detail}"}
    return {"ok": True, "auth": "ok"}


@app.get("/api/prompts")
def list_prompts() -> list[dict]:
    return [
        {
            "id": p.id,
            "version": p.version,
            "title": p.title,
            "status": p.status,
            "model": p.model,
            "effort": p.effort,
        }
        for p in prompts.available()
    ]


@app.get("/api/samples")
def list_samples() -> list[str]:
    if not SAMPLES_DIR.is_dir():
        return []
    return sorted(p.stem for p in SAMPLES_DIR.glob("*.svg"))


@app.get("/api/samples/{name}")
def get_sample(name: str) -> FileResponse:
    path = SAMPLES_DIR / f"{name}.svg"
    # `name` is client-supplied; resolve and confine it before reading.
    if not path.resolve().is_relative_to(SAMPLES_DIR.resolve()) or not path.is_file():
        raise HTTPException(status_code=404, detail=f"no sample {name!r}")
    return FileResponse(path, media_type="image/svg+xml")


@app.post("/api/turn")
async def take_turn(request: TurnRequest) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        try:
            settings = Settings.from_env()
        except RuntimeError as exc:
            yield _sse({"type": "error", "message": str(exc)})
            return
        async for event in turn.run(
            settings,
            request.svg,
            prompt_id=request.prompt_id,
            version=request.version,
        ):
            yield _sse(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/src", StaticFiles(directory=FRONTEND_DIR / "src"), name="src")
