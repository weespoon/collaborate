/**
 * Consuming the turn stream.
 *
 * `EventSource` can't POST, and we need to send a whole SVG document, so this
 * reads the response body and parses the event stream itself. That's a handful
 * of lines and it keeps the transport identical whether the backend is uvicorn
 * or a Worker.
 */

/**
 * @param {{svg: string, promptId?: string, version?: number}} request
 * @param {Record<string, (event: object) => void>} handlers keyed by event type
 * @param {{signal?: AbortSignal}} [options] aborting stops the read mid-stream
 */
export async function takeTurn(request, handlers, options = {}) {
  const response = await fetch('/api/turn', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      svg: request.svg,
      prompt_id: request.promptId ?? null,
      version: request.version ?? null,
    }),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`Server returned ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  // `reader.read()` rejects when the signal aborts, which is what stops a
  // stalled turn; releasing the lock lets the body be cancelled cleanly.
  options.signal?.addEventListener('abort', () => reader.cancel().catch(() => {}), {
    once: true,
  });

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Events are separated by a blank line; a partial one stays in the buffer.
    let split = buffer.indexOf('\n\n');
    while (split !== -1) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf('\n\n');

      const payload = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n');
      if (!payload) continue;

      let event;
      try {
        event = JSON.parse(payload);
      } catch {
        continue;
      }
      handlers[event.type]?.(event);
    }
  }
}
