"""Cloudflare Worker entrypoint.

Handles `/api/*` and hands everything else to the static-assets binding, which
serves the frontend - the same single-origin shape the local dev server has, so
the browser contract is identical either way.

Everything of substance is shared with local dev: `turn.run`, `prompts`,
`svgdoc`, and `claude` are the very modules `make dev` exercises. This file and
`transport_fetch.py` are the only Worker-specific code.
"""

import asyncio
import json
import re

from js import URL, AbortSignal, Object, Request, TextEncoder, TransformStream, fetch
from pyodide.ffi import to_js
from workers import Response, WorkerEntrypoint

from collaborate import claude, prompts, turn
from collaborate.bundle import SAMPLES
from collaborate.config import ANTHROPIC_MODELS_URL, Settings

from transport_fetch import post_sse

# Read off the binding object explicitly. `env` also carries bindings that are
# not configuration, so this is a whitelist rather than a sweep.
ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "PROMPT_ID",
    "PROMPT_VERSION",
    "REQUEST_TIMEOUT_S",
    "REFUSAL_FALLBACK",
)

SAMPLE_NAME = re.compile(r"[A-Za-z0-9_-]+")


def _js(obj):
    return to_js(obj, dict_converter=Object.fromEntries)


def _json(payload, status: int = 200) -> Response:
    return Response(
        json.dumps(payload),
        status=status,
        headers={"content-type": "application/json"},
    )


def _settings(env) -> Settings:
    mapping = {}
    for key in ENV_KEYS:
        value = getattr(env, key, None)
        if value is not None:
            mapping[key] = str(value)
    return Settings.from_env(mapping)


def _frame(event: dict) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def _sse(events) -> Response:
    """Wrap an async iterator of protocol events in a streaming Response.

    The local server hands the identical generator to Starlette's
    `StreamingResponse`; here it is a `TransformStream` whose writer is pumped
    by a task that outlives this function's return.
    """
    stream = TransformStream.new()
    writer = stream.writable.getWriter()
    encoder = TextEncoder.new()

    async def pump():
        try:
            async for event in events:
                await writer.write(encoder.encode(_frame(event)))
        except Exception as exc:  # noqa: BLE001 - must reach the client as an event
            await writer.write(
                encoder.encode(
                    _frame({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
                )
            )
        finally:
            await writer.close()

    asyncio.ensure_future(pump())

    return Response(
        stream.readable,
        headers={
            "content-type": "text/event-stream",
            "cache-control": "no-cache, no-transform",
            "x-accel-buffering": "no",
        },
    )


def _sse_error(message: str) -> Response:
    async def one():
        yield {"type": "error", "message": message}

    return _sse(one())


async def _health(env) -> dict:
    try:
        settings = _settings(env)
    except RuntimeError as exc:
        return {"ok": False, "message": str(exc)}

    # Unbilled auth probe. Unlike the dev server this never echoes the upstream
    # body - an API error can quote the key back, and there is no reason to
    # forward that to a browser.
    response = await fetch(
        ANTHROPIC_MODELS_URL,
        _js(
            {
                "method": "GET",
                "headers": claude.auth_headers(settings),
                "signal": AbortSignal.timeout(10_000),
            }
        ),
    )
    if response.status != 200:
        return {"ok": False, "auth": f"HTTP {response.status} from the Claude API"}
    return {"ok": True, "auth": "ok"}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = URL.new(request.url).pathname

        if path == "/api/health":
            return _json(await _health(self.env))

        if path == "/api/prompts":
            return _json(
                [
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
            )

        if path == "/api/samples":
            return _json(list(SAMPLES))

        if path.startswith("/api/samples/"):
            name = path[len("/api/samples/") :]
            if not SAMPLE_NAME.fullmatch(name) or name not in SAMPLES:
                return _json({"detail": f"no sample {name!r}"}, status=404)
            # Samples ship as static assets; keep the dev server's URL shape.
            target = URL.new(request.url)
            target.pathname = f"/samples/{name}.svg"
            return await self.env.ASSETS.fetch(Request.new(target.href))

        if path == "/api/turn":
            if request.method != "POST":
                return _json({"detail": "method not allowed"}, status=405)
            try:
                settings = _settings(self.env)
            except RuntimeError as exc:
                return _sse_error(str(exc))

            body = await request.json()
            data = body.to_py() if hasattr(body, "to_py") else dict(body)
            svg = data.get("svg") or ""
            if not svg:
                return _json({"detail": "svg is required"}, status=422)

            version = data.get("version")
            return _sse(
                turn.run(
                    settings,
                    svg,
                    prompt_id=data.get("prompt_id"),
                    version=int(version) if version is not None else None,
                    transport=post_sse,
                )
            )

        if path.startswith("/api/"):
            return _json({"detail": f"no route {path}"}, status=404)

        # Everything else is the frontend.
        return await self.env.ASSETS.fetch(request)
