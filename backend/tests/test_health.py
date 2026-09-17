"""The health endpoint's auth probe.

No test here touches the network: the probe's HTTP call is replaced with a
fake so the suite stays free and offline.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from collaborate import local_server

KEY = "sk-ant-test-key"


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """Stands in for `httpx.AsyncClient` as an async context manager."""

    seen_headers: dict[str, str] = {}

    def __init__(self, response=None, error=None, **_: object) -> None:
        self._response = response
        self._error = error

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def get(self, url: str, headers=None) -> _FakeResponse:
        type(self).seen_headers = dict(headers or {})
        if self._error is not None:
            raise self._error
        return self._response


def _probe(monkeypatch: pytest.MonkeyPatch, **kwargs) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.setattr(
        local_server.httpx, "AsyncClient", lambda **_: _FakeClient(**kwargs)
    )


def test_missing_key_is_reported_without_a_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def explode(**_: object):
        raise AssertionError("must not probe the API without a key")

    monkeypatch.setattr(local_server.httpx, "AsyncClient", explode)

    with TestClient(local_server.app) as client:
        body = client.get("/api/health").json()

    assert body["ok"] is False
    assert "ANTHROPIC_API_KEY is not set" in body["message"]


def test_valid_key_reports_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _probe(monkeypatch, response=_FakeResponse(200, {"data": []}))

    with TestClient(local_server.app) as client:
        body = client.get("/api/health").json()

    assert body == {"ok": True, "auth": "ok"}
    # The probe must authenticate, and must not send the streaming request's
    # accept header to a plain GET.
    assert _FakeClient.seen_headers["x-api-key"] == KEY
    assert "accept" not in _FakeClient.seen_headers


def test_rejected_key_surfaces_the_api_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _probe(
        monkeypatch,
        response=_FakeResponse(
            401, {"error": {"type": "authentication_error", "message": "invalid key"}}
        ),
    )

    with TestClient(local_server.app) as client:
        body = client.get("/api/health").json()

    assert body["ok"] is False
    assert "401" in body["auth"] and "invalid key" in body["auth"]


def test_non_json_error_body_falls_back_to_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _probe(monkeypatch, response=_FakeResponse(502, None, text="upstream boom"))

    with TestClient(local_server.app) as client:
        body = client.get("/api/health").json()

    assert body["ok"] is False
    assert "502" in body["auth"] and "upstream boom" in body["auth"]


def test_unreachable_api_does_not_blame_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _probe(monkeypatch, error=httpx.ConnectError("no route"))

    with TestClient(local_server.app) as client:
        body = client.get("/api/health").json()

    assert body["ok"] is False
    assert "could not reach the API" in body["auth"]


def test_health_never_echoes_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _probe(
        monkeypatch,
        response=_FakeResponse(403, {"error": {"message": f"key {KEY} is not allowed"}}),
    )

    with TestClient(local_server.app) as client:
        raw = client.get("/api/health").text

    # An API error message could quote the key back; it must not reach a client.
    assert KEY not in raw
