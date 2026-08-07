/**
 * SSE reader for the gateway's chat stream.
 *
 * Same wire format as the gateway SPA (`data: {...}` lines, terminated by
 * `data: [DONE]`), so this is deliberately a close port of `spa/src/composables/useSSE.js`
 * — keeping the two in sync is easier than inventing a second dialect.
 */
export async function* readSSE(reader) {
  const dec = new TextDecoder()
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    buf = buf.replace(/\r\n/g, '\n')

    let idx
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim()
      buf = buf.slice(idx + 1)
      // Blank lines and `: keepalive` comments are the gateway holding the
      // connection open during long tool calls — not data.
      if (!line || !line.startsWith('data:')) continue
      const p = line.slice(5).trim()
      if (p === '[DONE]' || !p) continue

      try {
        yield JSON.parse(p)
      } catch (_) {
        if (!p.startsWith('{') && !p.startsWith('[')) {
          yield { type: 'text', text: p }
        }
      }
    }
  }
}
