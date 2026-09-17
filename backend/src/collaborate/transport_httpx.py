"""The local HTTP transport: `httpx` streaming an SSE response.

This is the one piece of the Claude client that is platform-specific. The
Worker has its own version over JavaScript `fetch` (`worker/src/transport_fetch.py`);
both expose the same `post_sse` coroutine-generator signature, so everything in
`claude.py` is shared.

`httpx` is deliberately not a hard dependency of this package - Pyodide cannot
load httpx 0.x, so importing it at the package level would break the Worker.
It lives in the `local` extra and is only imported here.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .claude import ClaudeError

async def post_sse(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
) -> AsyncIterator[dict[str, Any]]:
    """POST and yield each SSE `data:` payload, already JSON-decoded."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code != 200:
                detail = (await response.aread()).decode("utf-8", "replace")
                raise ClaudeError(f"HTTP {response.status_code}: {detail[:800]}")

            data_lines: list[str] = []
            async for line in response.aiter_lines():
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

