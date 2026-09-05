/**
 * @vitest-environment jsdom
 */
import { describe, expect, it } from 'vitest'
import { fitDocxTables } from './renderBlobPreview'

describe('fitDocxTables', () => {
  it('wraps tables and clears Word absolute width hints', () => {
    const root = document.createElement('div')
    root.innerHTML = `
      <section class="docx">
        <table style="width: 500pt; min-width: 400px" width="800">
          <colgroup>
            <col style="width: 120pt" />
            <col style="width: 180pt" />
            <col width="90" />
          </colgroup>
          <tr>
            <th style="width: 120pt">A</th>
            <td style="width: 180pt; max-width: 200px">long cell</td>
            <td width="90">尾</td>
          </tr>
        </table>
      </section>
    `
    fitDocxTables(root)

    const wrap = root.querySelector('.docx-table-scroll')
    const table = root.querySelector('table')
    expect(wrap).toBeTruthy()
    expect(wrap?.contains(table!)).toBe(true)
    expect(table?.getAttribute('width')).toBeNull()
    expect(table?.style.width).toBe('')
    expect(table?.style.minWidth).toBe('')

    const cols = [...root.querySelectorAll('col')]
    expect(cols.every((c) => !c.getAttribute('width') && !(c as HTMLElement).style.width)).toBe(true)

    const cells = [...root.querySelectorAll('th, td')]
    expect(cells.every((c) => !c.getAttribute('width') && !(c as HTMLElement).style.width)).toBe(true)
    expect((cells[1] as HTMLElement).style.maxWidth).toBe('')
  })

  it('does not double-wrap an already wrapped table', () => {
    const root = document.createElement('div')
    root.innerHTML = `
      <div class="docx-table-scroll">
        <table style="width: 300pt"><tr><td>x</td></tr></table>
      </div>
    `
    fitDocxTables(root)
    expect(root.querySelectorAll('.docx-table-scroll')).toHaveLength(1)
    expect(root.querySelector('table')?.style.width).toBe('')
  })

  it('marks first row as header when Word emitted no <th>', () => {
    const root = document.createElement('div')
    root.innerHTML = `
      <table>
        <tr><td>位置</td><td>动作</td></tr>
        <tr><td>a</td><td>b</td></tr>
      </table>
    `
    fitDocxTables(root)
    expect(root.querySelector('tr')?.classList.contains('docx-table-header-row')).toBe(true)
    expect(root.querySelectorAll('.docx-table-header-row')).toHaveLength(1)
  })

  it('tags monospace spans inside cells as inline code', () => {
    const root = document.createElement('div')
    root.innerHTML = `
      <table>
        <tr>
          <td><span style="font-family: Consolas, monospace">DEFAULT_MAX</span></td>
          <td><span style="font-family: Microsoft YaHei">普通</span></td>
        </tr>
      </table>
    `
    fitDocxTables(root)
    const spans = [...root.querySelectorAll('span')]
    expect(spans[0]?.classList.contains('docx-inline-code')).toBe(true)
    expect(spans[1]?.classList.contains('docx-inline-code')).toBe(false)
  })
})
