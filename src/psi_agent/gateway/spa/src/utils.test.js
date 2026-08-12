import { describe, expect, it } from 'vitest'
import { loadHistory, renderMd, saveHistory } from './utils.js'

describe('renderMd tables', () => {
  it('renders a contiguous GFM table', () => {
    const html = renderMd('| a | b |\n|---|---|\n| 1 | 2 |')
    expect(html).toContain('<table>')
    expect(html).toContain('<th>a</th>')
    expect(html).toContain('data-md-table')
    expect(html).toContain('data-table-action="copy"')
    expect(html).not.toContain('| a |')
  })

  it('normalizes blank lines between header and separator', () => {
    const html = renderMd('| a | b |\n\n|---|---|\n| 1 | 2 |')
    expect(html).toContain('<table>')
    expect(html).not.toMatch(/\| a \| b \|/)
  })

  it('unwraps fenced code blocks that contain only a table', () => {
    const html = renderMd('```\n| a | b |\n|---|---|\n| 1 | 2 |\n```')
    expect(html).toContain('<table>')
    expect(html).not.toContain('<pre><code>| a | b |')
  })
})

describe('renderMd links', () => {
  it('opens markdown links in a new tab', () => {
    const html = renderMd('[docs](https://example.com/path)')
    expect(html).toContain('href="https://example.com/path"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it('opens autolinks in a new tab', () => {
    const html = renderMd('see https://example.com/auto')
    expect(html).toContain('href="https://example.com/auto"')
    expect(html).toContain('target="_blank"')
  })
})

describe('history stream warnings', () => {
  it('persists only normalized warning codes', () => {
    const values = new Map()
    const originalStorage = globalThis.localStorage
    globalThis.localStorage = {
      getItem: key => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: key => values.delete(key),
    }

    try {
      saveHistory('warnings', [{
        role: 'assistant',
        text: 'answer',
        files: [],
        warnings: ['output_file_unavailable', 'private_warning'],
        fatal: true,
      }])

      expect(loadHistory('warnings')).toEqual([expect.objectContaining({
        role: 'assistant',
        warnings: ['output_file_unavailable', 'stream_warning'],
      })])
      expect(values.get('gw-hist-warnings')).not.toContain('private_warning')
      expect(values.get('gw-hist-warnings')).not.toContain('fatal')
    } finally {
      if (originalStorage === undefined) delete globalThis.localStorage
      else globalThis.localStorage = originalStorage
    }
  })
})
