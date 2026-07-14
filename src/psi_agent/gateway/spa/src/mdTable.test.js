import { describe, expect, it } from 'vitest'
import {
  matrixToCsv,
  matrixToMarkdown,
  matrixToTsv,
  tableToMatrix,
  wrapMdTableHtml,
} from './mdTable.js'

describe('mdTable helpers', () => {
  it('serializes TSV / CSV / Markdown', () => {
    const matrix = [
      ['标题', '播放'],
      ['街霸6', '1.2万'],
    ]
    expect(matrixToTsv(matrix)).toBe('标题\t播放\n街霸6\t1.2万')
    expect(matrixToCsv(matrix)).toBe('标题,播放\n街霸6,1.2万')
    expect(matrixToMarkdown(matrix)).toContain('| 标题 | 播放 |')
    expect(matrixToMarkdown(matrix)).toContain('| --- | --- |')
  })

  it('escapes TSV cells with tabs/newlines', () => {
    expect(matrixToTsv([['a\tb', 'x']])).toBe('"a\tb"\tx')
  })

  it('reads DOM table cells', () => {
    const cell = (tag, text) => {
      const el = { textContent: text }
      return el
    }
    const row = (cells) => ({
      querySelectorAll: (sel) => (sel === 'th, td' ? cells : []),
    })
    const table = {
      querySelectorAll: (sel) => {
        if (sel !== 'tr') return []
        return [
          row([cell('th', 'A'), cell('th', 'B')]),
          row([cell('td', '1'), cell('td', '2')]),
        ]
      },
    }
    expect(tableToMatrix(table)).toEqual([['A', 'B'], ['1', '2']])
  })

  it('wraps table html with toolbar markers', () => {
    const html = wrapMdTableHtml('<table><tr><td>x</td></tr></table>')
    expect(html).toContain('data-md-table')
    expect(html).toContain('data-table-action="copy"')
    expect(html).toContain('data-table-action="download"')
  })
})
