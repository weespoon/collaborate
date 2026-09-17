# Conversations

Verbatim logs of the Claude Code sessions that built this project. They are
kept because the *reasoning* behind a decision is usually more useful later
than the decision itself — why the backend declares no dependencies, why the
Worker talks raw HTTP instead of using the SDK, why prompts are baked into a
generated module. The code shows what; these show why, and what was tried and
rejected.

They are logs, not documentation. Where a log and the code disagree, the code
is right — a log is a record of a moment, including the mistakes made in it.

[design-rationale.md](design-rationale.md) is the short version of why this
project exists and what the constraints chose for it. The logs below are the
raw record.

| Session | When | What it covers |
|---|---|---|
| [2026-09-17-1651-scaffold.md](2026-09-17-1651-scaffold.md) | 16:51–17:24 | Greenfield build: the spec, the FastAPI dev shell, the SVG.js frontend, `svgdoc` review rules, the first passing test suite. Includes tool calls and reasoning. |
| [2026-09-17-1726-worker-and-deploy.txt](2026-09-17-1726-worker-and-deploy.txt) | 17:26– | Recovering the first session from its JSONL, API key and Cloudflare-secret handling, the Python Worker port (Pyodide, `fetch` transport, vendored package), first deploy, and the Workers Build that kept overwriting it. |

The two sessions barely overlap — measured at 0.1% shared 8-grams — so both
are worth keeping.

## Provenance

The first session ran in the Claude Code VS Code extension and was recovered
afterwards from `~/.claude/projects/<slug>/<session-id>.jsonl`, which is where
Claude Code persists every session. The exporter is kept at
`~/.claude/bin/claude-export`:

    claude-export --list          # sessions for the current directory
    claude-export                 # newest session -> markdown
    claude-export --tools         # include thinking and tool calls

The second was written by `/export` from inside the session.

## Not included

The original proof-of-concept conversations — the manual SVG-editor
prototyping that produced the doodle prompt now at `prompts/doodle/v1.md` —
live in claude.ai and are not here. Their share links render client-side, so
they cannot be fetched by URL; getting them in means exporting from claude.ai
directly. `prompts/doodle/v1.md` is the surviving artifact of that work.

## A note on secrets

Both files were scanned before being committed: no API keys, no key-shaped
strings, no private key blocks, no credentials. This repository is public, so
anything added here should get the same treatment.
