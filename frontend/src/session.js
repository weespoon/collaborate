/**
 * Session persistence in localStorage.
 *
 * One session for now. Multi-session is a change of key suffix and a picker,
 * which is why everything here is namespaced rather than stored under one blob.
 */

const PREFIX = 'collaborate:session:';
const DEFAULT_ID = 'default';

export class Session {
  constructor(id = DEFAULT_ID) {
    this.key = PREFIX + id;
  }

  /** @returns {{svg: string, transcripts: string[]} | null} */
  load() {
    try {
      const raw = localStorage.getItem(this.key);
      if (!raw) return null;
      const data = JSON.parse(raw);
      return typeof data?.svg === 'string'
        ? { svg: data.svg, transcripts: data.transcripts ?? [] }
        : null;
    } catch {
      // Private browsing, blocked site data, or a corrupt entry. Losing the
      // saved doodle is survivable; failing to start is not.
      return null;
    }
  }

  save(svg, transcripts = []) {
    try {
      localStorage.setItem(this.key, JSON.stringify({ svg, transcripts }));
    } catch {
      /* ignore */
    }
  }

  clear() {
    try {
      localStorage.removeItem(this.key);
    } catch {
      /* ignore */
    }
  }

  static list() {
    try {
      return Object.keys(localStorage)
        .filter((key) => key.startsWith(PREFIX))
        .map((key) => key.slice(PREFIX.length));
    } catch {
      return [];
    }
  }
}
