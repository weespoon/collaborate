"""The turn pipeline and its SSE framing, with the model stubbed out.

Everything except the network round trip is exercised here, so a broken event
sequence shows up without spending a token.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from collaborate import claude, local_server, turn
from collaborate.config import Settings

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
BASE = (SAMPLES / "t02.svg").read_text(encoding="utf-8")

LAYER = """  <g id="ai-turn-1" data-author="ai" fill="none" stroke="#8a8a8a"
     stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M 300 300 C 320 310 340 300 360 290"/>
  </g>"""
REPLY = BASE.replace("</svg>", f"{LAYER}\n</svg>")

SETTINGS = Settings(api_key="test-key")


def fake_stream(*_events):
    async def stream(settings, prompt, svg, **_):
        for event in _events:
            yield event

    return stream


async def collect(monkeypatch, *events, svg: str = BASE) -> list[dict]:
    monkeypatch.setattr(claude, "stream_turn", fake_stream(*events))
    return [event async for event in turn.run(SETTINGS, svg)]


async def test_happy_path_event_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    events = await collect(
        monkeypatch,
        claude.ThinkingDelta("Two vertical lines and a crossbar — "),
        claude.ThinkingDelta("that reads as a letter H."),
        claude.Completed(REPLY, stop_reason="end_turn", usage={"output_tokens": 812}),
    )

    assert [e["type"] for e in events] == [
        "status",  # sent
        "status",  # thinking
        "thinking",
        "thinking",
        "status",  # drawing
        "svg",
        "done",
    ]

    thinking = "".join(e["text"] for e in events if e["type"] == "thinking")
    assert thinking.endswith("that reads as a letter H.")

    assert events[0]["prompt"] == "doodle v1"
    assert events[0]["model"] == "claude-opus-5"

    drawing = next(e for e in events if e["type"] == "svg")
    assert 'id="ai-turn-1"' in drawing["svg"]

    done = events[-1]
    assert done["strokes"] == {"ai": 1, "new_user": 5}
    assert done["usage"]["output_tokens"] == 812


async def test_contract_breach_discards_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = BASE.replace(
        "</svg>", f'{LAYER.replace("#8a8a8a", "#ff0000")}\n</svg>'
    )
    events = await collect(monkeypatch, claude.Completed(broken))

    types = [e["type"] for e in events]
    assert "svg" not in types  # the client's document is left alone
    assert types[-1] == "error"
    assert any("not grey" in e["message"] for e in events if e["type"] == "warning")


async def test_warnings_do_not_block_a_drawing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    over_budget = BASE.replace(
        "</svg>",
        LAYER.replace(
            '    <path d="M 300 300 C 320 310 340 300 360 290"/>',
            "\n".join(
                f'    <path d="M {n} {n} C {n+4} {n} {n+8} {n+3} {n+12} {n}"/>'
                for n in range(300, 380, 10)
            ),
        )
        + "\n</svg>",
    )
    events = await collect(monkeypatch, claude.Completed(over_budget))
    assert any(e["type"] == "warning" and "Over budget" in e["message"] for e in events)
    assert events[-1]["type"] == "done"


async def test_api_failure_becomes_one_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(settings, prompt, svg, **_):
        raise claude.ClaudeError("HTTP 429: rate limited")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(claude, "stream_turn", boom)
    events = [e async for e in turn.run(SETTINGS, BASE)]
    assert events[-1] == {"type": "error", "message": "HTTP 429: rate limited"}


async def test_invalid_outgoing_document_never_reaches_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def tripwire(settings, prompt, svg, **_):
        nonlocal called
        called = True
        yield claude.Completed(REPLY)

    monkeypatch.setattr(claude, "stream_turn", tripwire)
    events = [e async for e in turn.run(SETTINGS, "<svg><path d='M 0 0'")]
    assert not called
    assert events[-1]["type"] == "error"


def test_sse_framing_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bytes a browser actually receives."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        claude,
        "stream_turn",
        fake_stream(claude.ThinkingDelta("hm"), claude.Completed(REPLY)),
    )

    with TestClient(local_server.app) as client:
        with client.stream("POST", "/api/turn", json={"svg": BASE}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())

    blocks = [b for b in body.split("\n\n") if b.strip()]
    assert all(b.startswith("data: ") for b in blocks)
    parsed = [json.loads(b[len("data: ") :]) for b in blocks]
    assert [p["type"] for p in parsed][-2:] == ["svg", "done"]


def test_turn_without_a_key_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(local_server.app) as client:
        response = client.post("/api/turn", json={"svg": BASE})
        assert "ANTHROPIC_API_KEY is not set" in response.text


@pytest.mark.anyio
async def test_buffered_drawing_phase_reports_progress(monkeypatch) -> None:
    """The answer SVG is buffered, so without this the client sees dead air."""
    chunk = "x" * claude.PROGRESS_EVERY_CHARS
    events = await collect(
        monkeypatch,
        claude.TextProgress(len(chunk)),
        claude.TextProgress(len(chunk) * 2),
        claude.Completed(REPLY),
    )
    progress = [e for e in events if e["type"] == "progress"]
    assert [e["chars"] for e in progress] == [len(chunk), len(chunk) * 2]
