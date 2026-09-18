"""One turn of the game, as a stream of protocol events.

Platform-neutral on purpose: this yields plain dicts and knows nothing about
FastAPI or Workers. The local server wraps it in a `StreamingResponse`; the
Worker will wrap the identical generator in a ReadableStream.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from . import claude, prompts, svgdoc
from .config import Settings


async def run(
    settings: Settings,
    svg: str,
    *,
    prompt_id: str | None = None,
    version: int | None = None,
    transport: claude.Transport | None = None,
) -> AsyncIterator[dict[str, Any]]:
    started = time.monotonic()

    try:
        prompt = prompts.load(
            prompt_id or settings.default_prompt_id,
            version if version is not None else settings.default_prompt_version,
        )
    except (ValueError, FileNotFoundError) as exc:
        yield {"type": "error", "message": str(exc)}
        return

    try:
        svgdoc.parse(svg)
    except svgdoc.InvalidSVG as exc:
        yield {"type": "error", "message": f"Outgoing document is invalid: {exc}"}
        return

    yield {
        "type": "status",
        "phase": "sent",
        "prompt": f"{prompt.id} v{prompt.version}",
        "model": prompt.model,
    }

    response_text = ""
    usage: dict[str, Any] = {}
    thinking_seen = False

    try:
        async for event in claude.stream_turn(
            settings, prompt, svg, transport=transport
        ):
            if isinstance(event, claude.ThinkingDelta):
                if not thinking_seen:
                    thinking_seen = True
                    yield {"type": "status", "phase": "thinking"}
                yield {"type": "thinking", "text": event.text}
            elif isinstance(event, claude.TextProgress):
                yield {"type": "progress", "chars": event.chars}
            elif isinstance(event, claude.Completed):
                response_text = event.text
                usage = event.usage
    except claude.ClaudeError as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except Exception as exc:  # transport failures, timeouts, cancelled streams
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        return

    yield {"type": "status", "phase": "drawing"}

    try:
        result = svgdoc.review(svg, response_text)
    except svgdoc.InvalidSVG as exc:
        yield {"type": "error", "message": f"Returned document is invalid: {exc}"}
        return

    for warning in result.warnings:
        yield {"type": "warning", "message": warning}

    if not result.ok:
        for message in result.errors:
            yield {"type": "warning", "message": message}
        yield {
            "type": "error",
            "message": "Response broke the drawing contract; turn discarded.",
        }
        return

    yield {"type": "svg", "svg": result.svg}
    yield {
        "type": "done",
        "elapsed_s": round(time.monotonic() - started, 1),
        "strokes": {"ai": result.ai_strokes, "new_user": result.new_user_strokes},
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        },
    }
