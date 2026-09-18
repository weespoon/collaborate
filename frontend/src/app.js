/**
 * Wiring: editor + turn stream + session storage.
 */

import { Editor } from './editor.js';
import { Session } from './session.js';
import { takeTurn } from './stream.js';

const el = (id) => document.getElementById(id);
const ui = {
  canvas: el('canvas'),
  send: el('send'),
  undo: el('undo'),
  sample: el('sample'),
  status: el('status'),
  thinking: el('thinking'),
  log: el('log'),
  meta: el('prompt-meta'),
  hint: el('hint'),
};

const session = new Session();
const editor = new Editor(ui.canvas, { onChange: sync });

let busy = false;
let transcripts = session.load()?.transcripts ?? [];

// ---------------------------------------------------------------- rendering

function setStatus(text, live = false) {
  ui.status.textContent = text;
  ui.status.classList.toggle('live', live);
}

function log(message, kind = '') {
  const line = document.createElement('div');
  line.className = kind;
  line.textContent = message;
  ui.log.append(line);
  ui.log.scrollTop = ui.log.scrollHeight;
}

function renderTranscripts() {
  ui.thinking.textContent = '';
  if (transcripts.length === 0) {
    const placeholder = document.createElement('p');
    placeholder.className = 'placeholder';
    placeholder.textContent =
      "Claude's reasoning appears here while it draws.";
    ui.thinking.append(placeholder);
    return;
  }
  transcripts.forEach((text, index) => {
    if (index > 0) {
      const rule = document.createElement('hr');
      rule.className = 'turn-rule';
      ui.thinking.append(rule);
    }
    ui.thinking.append(document.createTextNode(text));
  });
}

function sync() {
  // An empty canvas has no history to belong to. Without this, undoing every
  // stroke leaves the transcripts and the log from a drawing that no longer
  // exists, and the next turn reads as a continuation of it.
  if (!busy && editor.isEmpty() && transcripts.length > 0) {
    transcripts = [];
    renderTranscripts();
    ui.log.textContent = '';
    setStatus('idle');
  }

  session.save(editor.serialize(), transcripts);

  const last = editor.lastKind();
  ui.undo.disabled = busy || last === null;
  ui.undo.textContent =
    last === 'ai' ? "Undo Claude's turn" : last === 'user' ? 'Undo stroke' : 'Undo';

  const pending = editor.newUserStrokes();
  ui.send.disabled = busy || pending === 0;

  if (busy) return;
  if (editor.isEmpty()) {
    ui.hint.textContent = 'Draw a few strokes, then send.';
  } else if (pending === 0) {
    ui.hint.textContent = 'Your move — Claude is waiting on a new stroke.';
  } else {
    const plural = pending === 1 ? 'stroke' : 'strokes';
    ui.hint.textContent = `${pending} new ${plural}; Claude will answer with at most ${pending}.`;
  }
}

// -------------------------------------------------------------------- turn

// A turn is a long streaming response from someone else's API. If it stalls -
// the connection drops without closing, the Worker dies mid-stream - `read()`
// never resolves and never rejects, so `busy` would stay true forever and the
// Send button would never come back. These two watchdogs are what guarantee
// the turn always ends, one way or another.
//
// They can afford to be patient now. The server emits `progress` while it
// buffers the answer document, so silence means silence; previously the entire
// drawing phase was silent and this timer was cutting off turns that were
// working fine. The ceiling sits above REQUEST_TIMEOUT_S in wrangler.jsonc on
// purpose - the server should give up first, because its failure arrives as an
// `error` event that says what went wrong, and this one can only say "gave up".
const STALL_MS = 120_000; // no event of any kind for this long
const LIMIT_MS = 480_000; // absolute ceiling on one turn

async function send() {
  if (busy) return;
  busy = true;
  editor.setLocked(true);
  ui.canvas.classList.add('busy');
  ui.log.textContent = '';
  setStatus('sending…', true);
  sync();

  const controller = new AbortController();
  let stalled = false;
  let stallTimer;
  const giveUp = (why) => {
    stalled = true;
    controller.abort(new DOMException(why, 'AbortError'));
  };
  const beat = () => {
    clearTimeout(stallTimer);
    stallTimer = setTimeout(() => giveUp('stalled'), STALL_MS);
  };
  const limitTimer = setTimeout(() => giveUp('took too long'), LIMIT_MS);
  beat();

  const outgoing = editor.serialize();
  // A stream can end without saying anything: when the Worker is killed
  // mid-turn the body just stops, `read()` reports done, and `takeTurn`
  // returns as if all were well. Without this the run would be reported as a
  // success that drew nothing.
  let settled = false;
  let live = '';
  const index = transcripts.length;
  transcripts.push('');

  const flush = () => {
    transcripts[index] = live;
    renderTranscripts();
    ui.thinking.scrollTop = ui.thinking.scrollHeight;
  };

  try {
    await takeTurn(
      { svg: outgoing },
      {
        status: (event) => {
          beat();
          setStatus(event.phase === 'sent' ? 'sent' : event.phase, true);
          if (event.prompt) ui.meta.textContent = `${event.prompt} · ${event.model}`;
        },
        thinking: (event) => {
          beat();
          live += event.text;
          flush();
        },
        progress: (event) => {
          beat();
          const kb = Math.round(event.chars / 1024);
          setStatus(kb > 0 ? `drawing… ${kb}kb` : 'drawing…', true);
        },
        warning: (event) => {
          beat();
          log(`warning: ${event.message}`, 'warn');
        },
        svg: (event) => {
          beat();
          settled = true;
          editor.load(event.svg);
        },
        error: (event) => {
          beat();
          settled = true;
          log(`error: ${event.message}`, 'error');
          transcripts.splice(index, 1);
          renderTranscripts();
          setStatus('failed');
        },
        done: (event) => {
          beat();
          const { ai, new_user: newUser } = event.strokes ?? {};
          log(`${event.elapsed_s}s · ${ai} strokes against your ${newUser}`);
          const cached = event.usage?.cache_read_input_tokens;
          if (cached) log(`${cached} input tokens read from cache`);
          setStatus('done');
        },
      },
      { signal: controller.signal },
    );
    if (!settled) {
      log(
        'the turn ended without a drawing — the server stopped mid-stream, ' +
          'nothing was changed, try again',
        'error',
      );
      transcripts.splice(index, 1);
      renderTranscripts();
      setStatus('failed');
    }
  } catch (error) {
    const message = stalled
      ? `the turn ${error.message ?? 'stalled'} — nothing was drawn, try again`
      : `error: ${error.message}`;
    log(message, 'error');
    transcripts.splice(index, 1);
    renderTranscripts();
    setStatus('failed');
  } finally {
    clearTimeout(stallTimer);
    clearTimeout(limitTimer);
    busy = false;
    editor.setLocked(false);
    ui.canvas.classList.remove('busy');
    sync();
  }
}

// -------------------------------------------------------------------- setup

ui.send.addEventListener('click', send);
ui.undo.addEventListener('click', () => {
  if (busy) return;
  if (editor.undo() === 'ai') {
    transcripts.pop();
    renderTranscripts();
  }
});

ui.sample.addEventListener('change', async () => {
  const name = ui.sample.value;
  if (!name) return;
  ui.sample.value = '';
  if (
    !editor.isEmpty() &&
    !confirm('Replace the current drawing with this sample?')
  )
    return;
  const response = await fetch(`/api/samples/${name}`);
  editor.load(await response.text());
  transcripts = [];
  renderTranscripts();
  ui.log.textContent = '';
  setStatus('idle');
});

async function boot() {
  const saved = session.load();
  if (saved?.svg) {
    try {
      editor.load(saved.svg);
    } catch {
      session.clear();
    }
  }
  renderTranscripts();
  sync();

  fetch('/api/samples')
    .then((r) => r.json())
    .then((names) => {
      for (const name of names) {
        ui.sample.append(new Option(name, name));
      }
    })
    .catch(() => {});

  fetch('/api/prompts')
    .then((r) => r.json())
    .then(([prompt]) => {
      if (prompt) {
        ui.meta.textContent = `${prompt.id} v${prompt.version} · ${prompt.model} · effort ${prompt.effort}`;
      }
    })
    .catch(() => {});

  const health = await fetch('/api/health')
    .then((r) => r.json())
    .catch(() => ({ ok: false, message: 'backend unreachable' }));
  if (!health.ok) log(health.message, 'error');
}

boot();
