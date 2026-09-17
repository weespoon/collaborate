# Prompts

Every prompt the backend can run lives here as a versioned markdown file. The
front end never sees these — it sends an SVG and a prompt id, and the Worker
resolves the rest.

## Layout

```
prompts/
└── <game-id>/
    ├── v1.md
    ├── v2.md
    └── ...
```

The directory name is the game id; the filename is the version. A request names
`{id, version}`; the backend keeps a default version per game so the client can
omit it.

| Game       | Status      | What it is                                    |
| ---------- | ----------- | --------------------------------------------- |
| `doodle`   | active (v1) | Turn-based collaborative drawing              |
| `sprouts`  | planned     | The pencil-and-paper graph game                |

## File format

YAML frontmatter, then the prompt body. The body is sent verbatim as the system
prompt — what's in the file is what Claude sees, whitespace included.

```yaml
---
id: doodle           # matches the directory name
version: 1           # matches the filename
title: Collaborative Doodling
status: active       # active | draft | retired
created: 2026-09-17
defaults:            # advisory: what this version was tuned against
  model: claude-opus-5
  thinking:
    type: adaptive
    display: summarized
  effort: high
  max_tokens: 64000
notes: >
  Why this version exists and what changed.
---
```

`defaults` travels with the prompt because a prompt and the settings it was
tuned under are one unit — a wording that works at `effort: high` is not
necessarily the same wording that works at `low`. The backend may override them;
the file records intent.

## Conventions

**Never edit a shipped version in place.** Once a version has produced drawings
you liked, it's a fixed reference point. Changes go in a new file, with `notes`
saying what moved and why — including changes that look purely cosmetic, since
whitespace and wording are exactly the variables being tested.

**Retire, don't delete.** Set `status: retired` and leave the file. Old sessions
in someone's `localStorage` were drawn under old rules, and being able to read
what those rules were is the point of keeping them.

**One prompt, one system message.** No composing a prompt from fragments at
runtime. If two games share rules, duplicate the text — the diffability of whole
files is worth more than the deduplication.

**The prompt owns the output contract.** Format rules, the grey-layer
convention, the stroke budget, the bad-input fallback: all of it is in the
prompt file, not split between the prompt and the Worker. The Worker validates
what comes back; it doesn't specify it.

## Adding a game

A new game is a new directory with a `v1.md`. For it to be playable it needs to
fit the existing pipe — SVG in, reasoning streamed, SVG out — which most
pencil-and-paper games do, since the board and the move history are both just
marks on the page. Games needing turn validation the model can't be trusted to
do itself (sprouts has real legality rules) will want a checker in the Worker,
which is the first thing this structure doesn't yet account for.
