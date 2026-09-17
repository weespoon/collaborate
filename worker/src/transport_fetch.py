"""The Worker's HTTP transport: JavaScript `fetch` plus a stream reader.

The mirror of `collaborate.transport_httpx`, which does the same over httpx.
These two functions are the only platform-specific code in the project -
everything in `collaborate.claude` is shared, and the SSE framing below is a
line-for-line port of the httpx version so the two cannot drift in behaviour.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from js import AbortSignal, Object, TextDecoder, fetch
from pyodide.ffi import to_js

from collaborate.claude import ClaudeError


def _js(obj: Any):
    return to_js(obj, dict_converter=Object.fromEntries)


async def post_sse(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
) -> AsyncIterator[dict[str, Any]]:
    """POST and yield each SSE `data:` payload, already JSON-decoded."""
    response = await fetch(
        url,
        _js(
            {
                "method": "POST",
                "headers": headers,
                "body": json.dumps(body),
                # Workers have no per-request timeout knob; this is the
                # equivalent of httpx's `timeout=`.
                "signal": AbortSignal.timeout(int(timeout * 1000)),
            }
        ),
    )

    if response.status != 200:
        detail = str(await response.text())
        raise ClaudeError(f"HTTP {response.status}: {detail[:800]}")

    reader = response.body.getReader()
    decoder = TextDecoder.new("utf-8")
    pending = ""  # decoded text not yet terminated by a newline
    data_lines: list[str] = []

    while True:
        chunk = await reader.read()
        if chunk.done:
            break
        # `stream: True` so a multi-byte character split across two network
        # chunks is held back rather than mangled.
        pending += decoder.decode(chunk.value, _js({"stream": True}))

        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            line = line.rstrip("\r")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
                continue
            if line == "" and data_lines:
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    yield json.loads(payload)
                except json.JSONDecodeError:
                    continue  # a keepalive or something we don't model
            # `event:` lines are redundant - the JSON carries its own `type`.
