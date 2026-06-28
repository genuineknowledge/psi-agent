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
