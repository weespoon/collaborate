/**
 * The drawing surface: pointer events in, one SVG document out.
 *
 * svg.js is used as a thin handle on real DOM nodes and nothing more. The
 * document is the game state and Claude is asked to reproduce it byte for byte,
 * so nothing here may reformat, round, or reorder what it didn't just draw —
 * which is exactly why this isn't a canvas library with an SVG export.
 */

export const SIZE = 512;

const USER_STROKE = {
  fill: 'none',
  stroke: '#000000',
  'stroke-width': '2',
  'stroke-linecap': 'round',
  'stroke-linejoin': 'round',
};

// --------------------------------------------------------------- smoothing
//
// A hand wiggles. Raw pointer samples carry that tremor at full amplitude, and
// every wiggle becomes another cubic segment in the `d` attribute — which is
// sent to the model, re-emitted by it, and sent again next turn. Path bytes are
// the single biggest cost driver in this app, so the pen is filtered in three
// stages before it becomes path data.

// 1. Drop samples closer together than this. Pointer events fire far denser
//    than a drawing needs; this is the cheap first cut.
const MIN_SAMPLE_DISTANCE = 3.5;

// 2. Exponential moving average over the sampled points — the tremor filter.
//    0 is no smoothing, 1 never moves. Raise it if hands still read as shaky;
//    past ~0.7 corners start rounding off visibly.
const SMOOTHING = 0.45;

// 3. Ramer-Douglas-Peucker: throw away points the curve doesn't need. In user
//    units on a 512 viewBox, so ~0.2% of the canvas — below what you can see,
//    and it typically removes half the points on anything but a tight scribble.
const SIMPLIFY_TOLERANCE = 1.0;

// Coordinates are clamped to this many decimals everywhere they're written.
// A 512 viewBox rendered at 512px makes the 4th decimal a ten-thousandth of a
// pixel: pure wire cost.
const PRECISION = 3;

const round = (n) => {
  const factor = 10 ** PRECISION;
  return Math.round(n * factor) / factor;
};

/** EMA over the point list. Endpoints are pinned so strokes start and end where
 *  the pen did — smoothing them drags the whole stroke inward. */
function smooth(points) {
  if (points.length < 3 || SMOOTHING <= 0) return points;
  const out = [points[0]];
  let { x, y } = points[0];
  for (let i = 1; i < points.length - 1; i += 1) {
    x += (points[i].x - x) * (1 - SMOOTHING);
    y += (points[i].y - y) * (1 - SMOOTHING);
    out.push({ x, y });
  }
  out.push(points[points.length - 1]);
  return out;
}

/** Perpendicular distance from `p` to the segment `a`-`b`. */
function pointLineDistance(p, a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (dx === 0 && dy === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  const t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy);
  const clamped = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (a.x + clamped * dx), p.y - (a.y + clamped * dy));
}

/** Ramer-Douglas-Peucker, iteratively — a long stroke would blow the stack. */
function simplify(points, tolerance) {
  if (points.length < 3) return points;
  const keep = new Uint8Array(points.length);
  keep[0] = 1;
  keep[points.length - 1] = 1;
  const stack = [[0, points.length - 1]];

  while (stack.length > 0) {
    const [first, last] = stack.pop();
    let worst = 0;
    let index = -1;
    for (let i = first + 1; i < last; i += 1) {
      const distance = pointLineDistance(points[i], points[first], points[last]);
      if (distance > worst) {
        worst = distance;
        index = i;
      }
    }
    if (worst > tolerance && index !== -1) {
      keep[index] = 1;
      stack.push([first, index], [index, last]);
    }
  }
  return points.filter((_, i) => keep[i]);
}

/** Catmull-Rom through the points, emitted as cubic beziers. */
function toPathData(points) {
  if (points.length === 0) return '';
  const [first] = points;
  if (points.length === 1) {
    // A tap is a dot: a hair of a segment under a round linecap.
    return `M ${round(first.x)} ${round(first.y)} l 0.01 0`;
  }

  const d = [`M ${round(first.x)} ${round(first.y)}`];
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? p2;
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d.push(
      `C ${round(c1x)} ${round(c1y)} ${round(c2x)} ${round(c2y)} ` +
        `${round(p2.x)} ${round(p2.y)}`,
    );
  }
  return d.join(' ');
}

/**
 * The pen pipeline, in one place so the preview and the committed stroke can
 * only ever differ by the stage that is deliberately skipped below.
 */
function strokePath(points, { simplified }) {
  const smoothed = smooth(points);
  return toPathData(
    simplified ? simplify(smoothed, SIMPLIFY_TOLERANCE) : smoothed,
  );
}

const DROP_ATTR = /^(xmlns:svgjs|svgjs:|xmlns$)/;

// Attributes whose values are pure geometry, safe to renumber. Anything else
// (colors, ids, viewBox) is copied through untouched.
const GEOMETRY_ATTR = new Set([
  'd',
  'points',
  'x',
  'y',
  'x1',
  'y1',
  'x2',
  'y2',
  'cx',
  'cy',
  'r',
  'rx',
  'ry',
  'width',
  'height',
]);

// Only decimals are matched, so integers are left exactly as written.
const DECIMAL = /-?\d*\.\d+/g;

/**
 * Clamp every decimal in a geometry attribute to PRECISION places.
 *
 * Strokes this editor drew are already rounded, but a sample file or a layer
 * Claude just returned can carry any precision at all, and `serialize` is the
 * one place every document passes through on its way to the wire.
 */
function clampPrecision(name, value) {
  if (!GEOMETRY_ATTR.has(name)) return value;
  return value.replace(DECIMAL, (match) => String(round(Number(match))));
}

function escapeAttr(value) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Stable, diff-friendly serialization — one element per line, 2-space indent. */
function serializeNode(node, indent) {
  const attrs = [...node.attributes]
    .filter((a) => !DROP_ATTR.test(a.name))
    .map((a) => `${a.name}="${escapeAttr(clampPrecision(a.name, a.value))}"`)
    .join(' ');
  const open = `${node.localName}${attrs ? ` ${attrs}` : ''}`;
  if (node.children.length === 0) return `${indent}<${open}/>`;
  const inner = [...node.children]
    .map((child) => serializeNode(child, `${indent}  `))
    .join('\n');
  return `${indent}<${open}>\n${inner}\n${indent}</${node.localName}>`;
}

export class Editor {
  /** @param {HTMLElement} host @param {{onChange?: () => void}} options */
  constructor(host, { onChange } = {}) {
    this.onChange = onChange ?? (() => {});
    this.root = SVG().addTo(host).viewbox(0, 0, SIZE, SIZE);
    this.node = this.root.node;
    this.locked = false;
    this.#bindPointer();
  }

  #bindPointer() {
    let points = null;
    let live = null;

    const toUserSpace = (event) => {
      const ctm = this.node.getScreenCTM();
      const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(
        ctm.inverse(),
      );
      return { x: point.x, y: point.y };
    };

    const start = (event) => {
      if (this.locked || event.button !== 0) return;
      this.node.setPointerCapture(event.pointerId);
      points = [toUserSpace(event)];
      live = this.root.path('').attr(USER_STROKE);
    };

    const move = (event) => {
      if (!points) return;
      const point = toUserSpace(event);
      const previous = points[points.length - 1];
      if (Math.hypot(point.x - previous.x, point.y - previous.y) < MIN_SAMPLE_DISTANCE)
        return;
      points.push(point);
      // Preview skips simplification: RDP re-picks which points it keeps as the
      // stroke grows, and that reads as the line twitching under the pen.
      live.plot(strokePath(points, { simplified: false }));
    };

    const end = () => {
      if (!points) return;
      live.plot(strokePath(points, { simplified: true }));
      points = null;
      live = null;
      this.onChange();
    };

    this.node.addEventListener('pointerdown', start);
    this.node.addEventListener('pointermove', move);
    this.node.addEventListener('pointerup', end);
    this.node.addEventListener('pointercancel', end);
    this.node.addEventListener('pointerleave', end);
  }

  setLocked(locked) {
    this.locked = locked;
  }

  /** The whole document, as it goes on the wire. */
  serialize() {
    const children = [...this.node.children]
      .map((child) => serializeNode(child, '  '))
      .join('\n');
    return [
      '<?xml version="1.0" encoding="utf-8"?>',
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${SIZE} ${SIZE}">`,
      children,
      '</svg>',
    ]
      .filter((line) => line !== '')
      .join('\n');
  }

  /** Replace everything with a parsed document (a sample, a save, an AI turn). */
  load(svgText) {
    const parsed = new DOMParser().parseFromString(svgText, 'image/svg+xml');
    if (parsed.querySelector('parsererror')) throw new Error('Could not parse SVG');
    const incoming = parsed.documentElement;

    this.clear();
    const viewBox = incoming.getAttribute('viewBox');
    if (viewBox) this.node.setAttribute('viewBox', viewBox);
    for (const child of [...incoming.children]) {
      this.node.appendChild(document.importNode(child, true));
    }
    this.onChange();
  }

  clear() {
    while (this.node.firstChild) this.node.removeChild(this.node.firstChild);
  }

  /**
   * Remove the last thing that happened.
   *
   * Claude only ever appends one group before `</svg>`, so the document is an
   * append-only stack and undo is a pop — of one stroke, or of a whole turn.
   */
  undo() {
    const last = this.node.lastElementChild;
    if (!last) return null;
    const kind = this.#kindOf(last);
    this.node.removeChild(last);
    this.onChange();
    return kind;
  }

  lastKind() {
    const last = this.node.lastElementChild;
    return last ? this.#kindOf(last) : null;
  }

  #kindOf(element) {
    return element.localName === 'g' && /^ai-turn-\d+$/.test(element.id ?? '')
      ? 'ai'
      : 'user';
  }

  isEmpty() {
    return this.node.children.length === 0;
  }

  /** New user strokes since Claude's last turn — the same count it budgets to. */
  newUserStrokes() {
    const children = [...this.node.children];
    let lastAi = -1;
    children.forEach((child, index) => {
      if (this.#kindOf(child) === 'ai') lastAi = index;
    });
    return children
      .slice(lastAi + 1)
      .reduce(
        (total, child) =>
          total + (child.getAttribute('d')?.match(/[Mm]/g)?.length || 1),
        0,
      );
  }
}
