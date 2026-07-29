import { describe, expect, it } from 'vitest'
import { renderMd } from './utils.js'

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
