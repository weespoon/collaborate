# Collaborate

A turn-based doodling game you play with Claude.

You draw a few pencil strokes in the browser. You press **Send**. Claude looks at
your drawing, thinks about it out loud while you watch, and answers with a few
grey strokes of its own — continuing your line, answering it, completing it.
Then it's your turn again.

The whole thing is one SVG document passed back and forth. Your marks are black,
Claude's are grey, and nobody ever erases anyone else's line.

---

## Why this is built the way it is

Three constraints shape every decision below.

**1. The good answers are slow.** The models that draw well take 20–30 seconds
per turn. That's far too long to show a spinner. So the reasoning is the
feature, not a side effect: Claude's thinking is streamed to the browser and
displayed while you wait. Watching it work out that your two curves might be a
pair of wings is most of the fun.

**2. The SVG is the entire game state.** There is no server-side session, no
database, no move list. Everything that has happened is in the document: black
elements are yours, `<g id="ai-turn-N">` groups are Claude's, and document order
is chronological order. That invariant does a lot of work — it makes undo a
`pop()`, it makes the backend stateless, and it's how Claude knows which of your
strokes are new (anything after the last `ai-turn-` group).

**3. The prompt is the product.** Getting Claude to behave as a *partner* rather
than an illustrator — to add three strokes instead of finishing the picture, to
respect your lines, to leave room for your reply — is almost entirely prompt
work. So prompts are versioned files under [prompts/](prompts/), live only on
the backend, and never touch the wire.

---

## A turn, end to end

```
browser                          worker (python)                 claude api
   │                                    │                             │
   │  POST /api/turn  {svg, prompt_id}  │                             │
   ├───────────────────────────────────>│                             │
   │                                    │  messages.stream(...)       │
   │                                    │  system = prompt file       │
   │                                    │  thinking: adaptive,        │
   │                                    │            display=summarized
   │                                    ├────────────────────────────>│
   │                                    │                             │
   │  <── SSE: {"type":"thinking", ...} │<──── thinking_delta ────────┤
   │  <── SSE: {"type":"thinking", ...} │<──── thinking_delta ────────┤
   │        (rendered live in the UI)   │                             │
   │                                    │<──── text_delta ────────────┤
   │  <── SSE: {"type":"svg", ...}      │  (buffered, validated)      │
   │  <── SSE: {"type":"done", ...}     │                             │
   │                                    │                             │
   │  render new grey layer, push       │                             │
   │  onto history, save to localStorage│                             │
```

The browser sends an SVG and gets back an SVG. It never sees a prompt, a model
name, or an API key.

### Event protocol

One `POST /api/turn` returns `text/event-stream`. Every event is a single JSON
object with a `type`:

| `type`     | Payload                   | Meaning                                       |
| ---------- | ------------------------- | --------------------------------------------- |
| `status`   | `{ "phase": "…" }`        | Coarse progress: `sent`, `thinking`, `drawing` |
| `thinking` | `{ "text": "…" }`         | Incremental summarized reasoning, append it   |
| `warning`  | `{ "message": "…" }`      | The turn bent a rule but is still usable      |
| `svg`      | `{ "svg": "<svg …>" }`    | The complete returned document                |
| `error`    | `{ "message": "…" }`      | Terminal; the turn did not happen             |
| `done`     | `{ "usage": { … } }`      | Stream is over                                |

Two deliberate asymmetries:

- **Thinking streams token by token. The SVG does not.** A half-written `<path>`
  is not a drawing, it's a rendering glitch. The worker buffers the text blocks
  and emits `svg` once, after validating it parses and contains exactly one new
  `ai-turn-` group.
- **`error` is always terminal and always leaves the client's document
  untouched.** A failed turn is a no-op; you just press Send again.

`warning` is the prompt-development channel. The backend checks every response
against the rules the prompt sets out and sorts the results into two piles:
things that make the turn unusable (no new layer, a `<rect>`, a coloured stroke)
become an `error` and the turn is discarded, while things that are merely off
(over budget, existing marks nudged, a stray code fence) become warnings and the
drawing lands anyway. Which warnings a prompt version produces, and how often, is
the signal you tune against.

---

## Scope

This tool does four things. It is supposed to be frustrating how few things it
does — the constraint is what makes it a conversation rather than an art app.

- **Draw** — freehand pencil. Black, one stroke width. No shapes, no fill, no
  text, no color picker, no layers panel.
- **Send** — hand the document to your collaborator.
- **Undo** — remove the last action, whether that's your last stroke or Claude's
  entire last turn. Repeatable all the way back to a blank page.
- **Sessions** — the current drawing survives a reload. Eventually: keep more
  than one.

Explicitly not in scope: selection, transform, zoom/pan, export beyond plain
SVG, real-time multiplayer, accounts.

### Undo

Because Claude only ever *appends* one group before `</svg>` and never touches
what's already there, the document is an append-only stack of nodes. So:

| Action        | Undo effect                                       |
| ------------- | ------------------------------------------------- |
| Your stroke   | Pop one element.                                   |
| Claude's turn | Pop one `<g id="ai-turn-N">` — the whole reply.    |

No diffing, no snapshots, no redo (at least for now — redo would mean keeping
popped nodes around, which is cheap if we want it later).

---

## Architecture

### Front end

A single-page SVG editor built on **`svg.js`**, served from Cloudflare as static
assets.

The document *is* the deliverable here, and fidelity matters: Claude is
instructed to reproduce the input byte for byte, so the editor must not
reformat, round, or reorder anything on load. `svg.js` is a thin handle on real
DOM SVG nodes, which is exactly that. `fabric.js` maintains its own canvas
object model and treats SVG as an import/export format, which would put a lossy
round-trip in the middle of the one thing this app must not lose.

Serialization is ours rather than `XMLSerializer`'s, for the same reason: one
element per line, two-space indent, `svg.js`'s own bookkeeping attributes
stripped. Stable output means a diff between turns shows only what Claude added.

Persistence is `localStorage`: the SVG string plus a small action log for undo.
Multi-session support is a key-prefix change, not a redesign.

### Back end

A Cloudflare Worker written in Python. It:

- owns the prompts, the API key, and the model configuration
- accepts an SVG, streams back reasoning, returns an SVG
- validates what comes back before handing it to the browser
- is stateless — no storage bindings needed for v1

**Model configuration:** `claude-opus-5` with `thinking: {"type": "adaptive",
"display": "summarized"}` and streaming.

That `display` flag is load-bearing and easy to get wrong. On Opus 5 the default
is `"omitted"` — thinking still happens and is still billed, but the blocks
arrive with empty text, which looks exactly like a 25-second hang. `"summarized"`
is what makes the thinking-out-loud UI possible at all.

The system prompt is stable across every request, so it gets a `cache_control`
breakpoint; the per-turn SVG goes after it.

### The Python-on-Workers constraint

Worth knowing before writing code: Cloudflare's Python Workers run under
Pyodide, and only async HTTP libraries work — the docs name `aiohttp` and
`httpx2`, or JavaScript `fetch()` through the FFI. The official `anthropic`
Python SDK (currently 0.x) is built on `httpx` 0.x, so **assume it cannot simply
be imported into a Python Worker until we've verified otherwise.**

So [`backend/src/collaborate/claude.py`](backend/src/collaborate/claude.py)
talks to the Messages API over **raw HTTP and parses the SSE stream itself**,
even locally where the SDK would work fine. We consume exactly two event shapes
(`thinking_delta`, `text_delta`), so it's a small amount of code — and running
it locally means the part most likely to break in production is the part we
exercise every day, rather than something discovered after the port.

`_post_sse` is the only function in that module that touches a transport.
Porting to a Worker means reimplementing it against `fetch` and nothing else.

The escape hatch, if this turns into a time sink: put the Python service in a
container (Cloudflare Containers, or any Python host) with a thin Worker in
front, and use the official SDK. Costs a second deployment target and cold
starts. The browser contract doesn't change either way.

---

## Prompts

Prompts are versioned markdown files with YAML frontmatter, loaded by id and
version. See [prompts/README.md](prompts/README.md) for the format and
conventions. The current one is
[prompts/doodle/v1.md](prompts/doodle/v1.md) — the prompt that produced good
results in manual prototyping.

They're separate files rather than string literals for two reasons: prompt
versions need to be diffable and A/B-able against each other, and the same
machinery should eventually host other pencil-and-paper games — sprouts, dots
and boxes, exquisite corpse — which are the same architecture with different
rules.

---

## Running it locally

Needs Python 3.11+ and an Anthropic API key.

```sh
make setup                      # venv + deps, and copies backend/.env.example
$EDITOR backend/.env            # add ANTHROPIC_API_KEY
make dev                        # http://localhost:8787
make test                       # 27 tests, no API key needed
```

`make dev` runs uvicorn, which serves both the API and the frontend from one
origin — the same shape Cloudflare will have (Worker + static assets), and the
reason there is no CORS configuration anywhere in the codebase. Port 8787 is
wrangler's default, so the URL won't change when this moves.

The dev page has a **sample** picker wired to [samples/](samples/), so you can
load a real starting document instead of drawing one every time you want to test
a prompt change.

**What the local server is and isn't:** only
[`local_server.py`](backend/src/collaborate/local_server.py) is throwaway. It
adapts a platform-neutral async generator — [`turn.run`](backend/src/collaborate/turn.py),
which yields the protocol events above as plain dicts — to a FastAPI
`StreamingResponse`. The Worker will wrap that same generator in a
ReadableStream. Everything else (`claude`, `prompts`, `svgdoc`, `turn`) is meant
to port unchanged.

The tests stub the model out, so the whole pipeline — event ordering, contract
checking, SSE framing on the wire — is verifiable without spending a token.

---

## Repo layout

```
collaborate/
├── Makefile               # setup / dev / test
├── prompts/
│   ├── README.md          # format + conventions
│   └── doodle/v1.md       # the working prompt
├── samples/               # starting documents for prototyping
│   └── t01.svg t02.svg t03.svg
├── backend/
│   ├── pyproject.toml
│   ├── .env.example
│   ├── src/collaborate/
│   │   ├── claude.py      # messages api over raw http + sse   ─┐
│   │   ├── prompts.py     # prompt loading / registry           │ ports to
│   │   ├── svgdoc.py      # contract checking on responses      │ the worker
│   │   ├── turn.py        # one turn as a stream of events     ─┘
│   │   ├── config.py      # settings from the environment
│   │   └── local_server.py # fastapi dev shell — throwaway
│   └── tests/
└── frontend/
    ├── index.html
    └── src/
        ├── app.js         # wiring
        ├── editor.js      # pointer events -> <path>, serialization, undo
        ├── session.js     # localStorage
        ├── stream.js      # SSE consumption
        └── style.css
```

Prompts live at the repo root, not inside `backend/`, because they're the
project's source of truth rather than an implementation detail. They'll need a
bundling step to reach the Worker.

---

## Open decisions

- **How prompt files get into the Worker bundle** — wrangler text modules, or a
  build step that generates a Python module. YAML parsing is currently a
  local-only dependency, which the build step would make permanent.
- **Whether `httpx2` is a drop-in for `httpx`** in `_post_sse`. Starlette
  already warns that `httpx` is deprecated in its test client and points at
  `httpx2`, so this may resolve itself.
- **Stroke smoothing.** Currently Catmull-Rom through samples thinned to 2.5
  units apart. It looks fine; whether it's what reads best *to the model* is
  untested, and it changes what Claude sees on every turn.
- **Whether thinking transcripts are kept** in the saved session. Right now they
  are, and undoing an AI turn drops its transcript with it.
- **Effort level.** `high` is the default; `xhigh` may draw better. Measurable
  now that warnings are counted.
- **Whether to keep user strokes flat** or group them per turn. Flat is what
  makes "everything after the last `ai-turn-` group is new" true, so grouping
  would need the budget rule rethought.

## Deploying to Cloudflare

The Worker lives in [worker/](worker/) and runs the same `collaborate` package
the dev server does. Only two files are platform-specific:
[`worker/src/entry.py`](worker/src/entry.py) (routing + the SSE
`TransformStream`) and
[`worker/src/transport_fetch.py`](worker/src/transport_fetch.py) (`fetch` in
place of httpx). `collaborate` itself declares **no dependencies** so it can be
bundled into Pyodide; httpx, PyYAML, FastAPI and uvicorn live in its `local`
extra and never reach the Worker.

```sh
make worker-dev                 # Pyodide locally, like production, on :8788
wrangler login                  # once
make deploy                     # bundle + wheel + assets + pywrangler deploy
cd worker && wrangler secret put ANTHROPIC_API_KEY
```

Two build steps run before every deploy, both generated and gitignored:

- `make bundle` bakes `prompts/` and the sample list into
  `backend/src/collaborate/bundle.py`, because Pyodide has no filesystem to walk
  and no PyYAML. The markdown files stay canonical.
- `make wheel` builds `collaborate` as a wheel. pywrangler installs into the
  Pyodide environment with `--no-build`, so a source tree is rejected.
- `make dist` assembles `worker/public/` (frontend + sample SVGs) for the
  static-assets binding. `run_worker_first: ["/api/*"]` keeps the API ahead of
  asset routing; everything else falls through to the assets.

**Configuration split:** non-secret values (`PROMPT_ID`, `PROMPT_VERSION`) are
plain `vars` in [worker/wrangler.jsonc](worker/wrangler.jsonc) and committed.
The API key is a secret, set with `wrangler secret put` and never in the repo —
this is a public repository. Locally the same value lives in `backend/.env`
(uvicorn) and `worker/.dev.vars` (pywrangler), both gitignored.

## Conversations

The Claude Code sessions that built this are kept verbatim in
[conversations/](conversations/) — the reasoning behind the architecture, and
what was tried and rejected along the way. See
[conversations/README.md](conversations/README.md).

## Status

Runs end to end both locally and as a Cloudflare Worker under Pyodide: draw,
send, watch it think, receive a layer, undo. Verified in the Worker — streaming
thinking arrives ~5s in, the system prompt caches (`cache_read_input_tokens`
confirmed non-zero on the second turn), and a turn costs roughly $0.04-0.05 at
`effort: high`.

Not yet deployed to a Cloudflare account — `make deploy` is wired and untested
against the real platform, pending `wrangler login`.
