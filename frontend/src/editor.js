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

// Pointer events fire far denser than a drawing needs. Dropping samples closer
// than this keeps path data small — it's sent to the model on every turn.
const MIN_SAMPLE_DISTANCE = 2.5;

const round = (n) => Math.round(n * 1000) / 1000;

/** Catmull-Rom through the sampled points, emitted as cubic beziers. */
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

const DROP_ATTR = /^(xmlns:svgjs|svgjs:|xmlns$)/;

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
    .map((a) => `${a.name}="${escapeAttr(a.value)}"`)
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
      live.plot(toPathData(points));
    };

    const end = () => {
      if (!points) return;
      live.plot(toPathData(points));
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
