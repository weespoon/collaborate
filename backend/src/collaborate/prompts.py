"""Loading versioned prompt files from `prompts/`.

Parsing YAML frontmatter is a local/build-time concern only. When this ships to
a Worker the same registry gets populated from a generated module (the Pyodide
bundle has no filesystem to walk), so keep `Prompt` free of any path state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# backend/src/collaborate/prompts.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPTS_DIR = REPO_ROOT / "prompts"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    title: str
    status: str
    body: str
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def model(self) -> str:
        return self.defaults.get("model", "claude-opus-5")

    @property
    def thinking(self) -> dict[str, Any]:
        return self.defaults.get(
            "thinking", {"type": "adaptive", "display": "summarized"}
        )

    @property
    def effort(self) -> str:
        return self.defaults.get("effort", "high")

    @property
    def max_tokens(self) -> int:
        return int(self.defaults.get("max_tokens", 64000))


def parse(text: str, *, source: str = "<string>") -> Prompt:
    match = _FRONTMATTER.match(text)
    if not match:
        raise ValueError(f"{source}: missing YAML frontmatter delimited by ---")

    # Imported here, not at module scope: parsing frontmatter is a local and
    # build-time concern, and PyYAML is not in the Worker bundle.
    import yaml

    meta = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)

    missing = [k for k in ("id", "version", "title") if k not in meta]
    if missing:
        raise ValueError(f"{source}: frontmatter missing {', '.join(missing)}")

    return Prompt(
        id=str(meta["id"]),
        version=int(meta["version"]),
        title=str(meta["title"]),
        status=str(meta.get("status", "draft")),
        body=body,
        defaults=meta.get("defaults") or {},
    )


def _bundled() -> dict[tuple[str, int], dict[str, Any]]:
    """Prompts baked in by `make bundle`, for environments with no filesystem."""
    try:
        from .bundle import PROMPTS
    except ImportError:
        return {}
    return PROMPTS


def from_bundle(prompt_id: str, version: int) -> Prompt | None:
    entry = _bundled().get((prompt_id, int(version)))
    return None if entry is None else Prompt(**entry)


def load(prompt_id: str, version: int, *, prompts_dir: Path | None = None) -> Prompt:
    """Load `prompts/<id>/v<version>.md`, or the bundled copy in a Worker."""
    root = prompts_dir or PROMPTS_DIR
    # `prompt_id` reaches us from the client, so it never gets to shape a path.
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", prompt_id):
        raise ValueError(f"invalid prompt id: {prompt_id!r}")

    path = root / prompt_id / f"v{int(version)}.md"
    if not path.is_file():
        # A Worker has no `prompts/` to read - the generated bundle is the
        # only source there, and the only place this falls back to.
        bundled = from_bundle(prompt_id, version)
        if bundled is not None:
            return bundled
        raise FileNotFoundError(f"no prompt for {prompt_id} v{version}")

    prompt = parse(path.read_text(encoding="utf-8"), source=str(path))
    if (prompt.id, prompt.version) != (prompt_id, int(version)):
        raise ValueError(
            f"{path}: frontmatter says {prompt.id} v{prompt.version}, "
            f"path says {prompt_id} v{version}"
        )
    return prompt


def available() -> list[Prompt]:
    """Every prompt on disk, for the dev UI's picker - or every bundled one."""
    if not PROMPTS_DIR.is_dir():
        return [Prompt(**e) for e in _bundled().values()]

    found = []
    for path in sorted(PROMPTS_DIR.glob("*/v*.md")):
        try:
            found.append(parse(path.read_text(encoding="utf-8"), source=str(path)))
        except ValueError:
            continue  # a malformed draft shouldn't break the listing
    return found
