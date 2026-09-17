"""Messages API client, spoken as raw HTTP + SSE.

Deliberately not the `anthropic` SDK. Cloudflare's Python Workers run under
Pyodide, where only async HTTP libraries work (`aiohttp`, `httpx2`, or JS
`fetch` over the FFI) and the SDK's `httpx` 0.x dependency is not one of them.
Parsing the event stream by hand is the part most likely to bite us in
production, so we run exactly that code locally rather than discovering it
after the port.

`_post_sse` is the only platform-specific function in this file; porting means
reimplementing it and nothing else.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from .config import ANTHROPIC_API_URL, ANTHROPIC_VERSION, Settings
from .prompts import Prompt

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeError(RuntimeError):
    """A call that never produced a usable turn."""


@dataclass
class ThinkingDelta:
    text: str


@dataclass
class TextDelta:
    text: str


@dataclass
class Completed:
    text: str
    stop_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


ClaudeEvent = ThinkingDelta | TextDelta | Completed


def build_request(settings: Settings, prompt: Prompt, svg: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": prompt.model,
        "max_tokens": prompt.max_tokens,
        "stream": True,
        # A breakpoint on the system prompt: it is byte-identical on every turn
        # while the SVG after it grows. Whether it actually caches depends on
        # the prompt clearing the model's minimum cacheable prefix - watch
        # `cache_read_input_tokens` in the `done` event rather than assuming.
        "system": [
            {
                "type": "text",
                "text": prompt.body,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "thinking": prompt.thinking,
        "output_config": {"effort": prompt.effort},
        "messages": [{"role": "user", "content": svg}],
    }
    if settings.refusal_fallback:
        body["fallbacks"] = "default"
    return body


def auth_headers(settings: Settings) -> dict[str, str]:
    """Just the credentials - what any request to the API needs."""
    return {
        "x-api-key": settings.api_key,
        "anthropic-version": ANTHROPIC_VERSION,
    }


def build_headers(settings: Settings) -> dict[str, str]:
    headers = {
        **auth_headers(settings),
        "content-type": "application/json",
        "accept": "text/event-stream",
    }
    if settings.refusal_fallback:
        headers["anthropic-beta"] = REFUSAL_FALLBACK_BETA
    return headers



# A transport is `(url, headers, body, timeout) -> async iterator of dicts`.
# `transport_httpx` is the local one; the Worker passes its `fetch` version.
Transport = Callable[[str, dict[str, str], dict[str, Any], float], AsyncIterator[dict[str, Any]]]


def default_transport() -> Transport:
    """Import the httpx transport lazily - it is absent in a Worker."""
    from .transport_httpx import post_sse

    return post_sse


async def stream_turn(
    settings: Settings,
    prompt: Prompt,
    svg: str,
    *,
    transport: Transport | None = None,
) -> AsyncIterator[ClaudeEvent]:
    """Run one turn, yielding reasoning as it arrives and the SVG at the end."""
    text_parts: list[str] = []
    stop_reason: str | None = None
    usage: dict[str, Any] = {}

    post_sse = transport or default_transport()
    stream = post_sse(
        ANTHROPIC_API_URL,
        build_headers(settings),
        build_request(settings, prompt, svg),
        settings.request_timeout_s,
    )

    async for event in stream:
        kind = event.get("type")

        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "thinking_delta":
                if chunk := delta.get("thinking"):
                    yield ThinkingDelta(chunk)
            elif delta.get("type") == "text_delta":
                # Buffered, never streamed on: half a <path> is a rendering
                # glitch, not a drawing.
                if chunk := delta.get("text"):
                    text_parts.append(chunk)

        elif kind == "message_start":
            usage.update((event.get("message") or {}).get("usage") or {})

        elif kind == "message_delta":
            stop_reason = (event.get("delta") or {}).get("stop_reason", stop_reason)
            usage.update(event.get("usage") or {})

        elif kind == "error":
            err = event.get("error") or {}
            raise ClaudeError(
                f"{err.get('type', 'error')}: {err.get('message', 'unknown')}"
            )

    if stop_reason == "refusal":
        raise ClaudeError(
            "The model declined this request (stop_reason: refusal). "
            "Set REFUSAL_FALLBACK=1 to route refusals to a fallback model."
        )
    if stop_reason == "max_tokens":
        raise ClaudeError(
            "Response hit max_tokens; the returned SVG is truncated. "
            "Raise max_tokens in the prompt's defaults."
        )

    yield Completed("".join(text_parts), stop_reason=stop_reason, usage=usage)
