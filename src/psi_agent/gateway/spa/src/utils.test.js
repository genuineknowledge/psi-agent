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
