"""Runtime configuration.

Everything here comes from a mapping of environment variables, so one module
covers both places this app runs:

    local dev    `os.environ`, populated from `backend/.env` by `make dev`
    deployed     a Cloudflare Worker's `env` bindings, where the key is a
                 secret set with `wrangler secret put` (see README)

The Worker entrypoint hands `from_env` a plain dict built from `env` rather
than shimming `os.environ`, so there is one obvious path in and no import-time
global to get wrong.

This repo is public: the API key has no default, is never written to a log or
a response, and `backend/.env` is gitignored.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
# Unbilled - used by the health check to prove the key authenticates.
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"

MISSING_KEY = (
    "ANTHROPIC_API_KEY is not set.\n"
    "  local:    put it in backend/.env (copy backend/.env.example), or "
    "export it before `make dev`\n"
    "  deployed: wrangler secret put ANTHROPIC_API_KEY\n"
    "Create a key at https://console.anthropic.com/settings/keys"
)
PLACEHOLDER_KEY = (
    "ANTHROPIC_API_KEY is still the placeholder from backend/.env.example. "
    "Replace it with a real key from "
    "https://console.anthropic.com/settings/keys"
)


@dataclass(frozen=True)
class Settings:
    # repr=False: a frozen dataclass prints every field, so without this the
    # key rides along in any traceback or debug print of a Settings object.
    api_key: str = field(repr=False)
    # Default game + prompt version served when the client doesn't name one.
    default_prompt_id: str = "doodle"
    default_prompt_version: int = 1
    # Wall-clock ceiling for one turn. Turns take 20-30s; this is the "something
    # is wrong" limit, not the expected duration.
    request_timeout_s: float = 180.0
    # Opt-in server-side refusal fallback. A doodle prompt is very unlikely to
    # trip a safety classifier, and this adds a beta header to the request, so
    # it's off until someone has a reason to turn it on. Refusal *handling*
    # (stop_reason == "refusal") is always active regardless.
    refusal_fallback: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Read settings from `env`, defaulting to the process environment."""
        env = os.environ if env is None else env
        key = (env.get("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            raise RuntimeError(MISSING_KEY)
        # `make setup` copies .env.example, so an older checkout can hold the
        # key-shaped placeholder rather than an empty value. Catch that exact
        # string instead of at a 401 from the API - no length heuristic, which
        # would also reject the short keys the tests construct.
        if key.endswith("..."):
            raise RuntimeError(PLACEHOLDER_KEY)
        return cls(
            api_key=key,
            default_prompt_id=env.get("PROMPT_ID", "doodle"),
            default_prompt_version=int(env.get("PROMPT_VERSION", "1")),
            request_timeout_s=float(env.get("REQUEST_TIMEOUT_S", "180")),
            refusal_fallback=env.get("REFUSAL_FALLBACK", "").lower()
            in ("1", "true", "yes"),
        )
