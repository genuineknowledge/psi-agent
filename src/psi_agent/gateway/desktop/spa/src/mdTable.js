/** Helpers for chat-bubble Markdown tables (copy / download). */

function cellText(cell) {
  return (cell?.textContent || '').replace(/\u00a0/g, ' ').trim()
}

/** Read a DOM ``<table>`` into a matrix of cell strings (header first). */
export function tableToMatrix(table) {
  if (!table) return []
  const rows = []
  table.querySelectorAll('tr').forEach((tr) => {
    const cells = [...tr.querySelectorAll('th, td')].map(cellText)
    if (cells.length) rows.push(cells)
  })
  return rows
}

function escapeTsvCell(value) {
  const s = String(value ?? '')
  if (/[\t\n\r"]/.test(s)) return `"${s.replace(/"/g, '""')}"`
  return s
}

/** Tab-separated values — pastes cleanly into Excel / Sheets. */
export function matrixToTsv(matrix) {
  return matrix.map((row) => row.map(escapeTsvCell).join('\t')).join('\n')
}

function escapeCsvCell(value) {
  const s = String(value ?? '')
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`
  return s
}

export function matrixToCsv(matrix) {
  return matrix.map((row) => row.map(escapeCsvCell).join(',')).join('\n')
}

/** Pipe Markdown table (GFM). */
export function matrixToMarkdown(matrix) {
  if (!matrix.length) return ''
  const width = Math.max(...matrix.map((r) => r.length))
  const norm = matrix.map((r) => {
    const row = r.slice()
    while (row.length < width) row.push('')
    return row.map((c) => String(c ?? '').replace(/\|/g, '\\|'))
  })
  const header = norm[0]
  const sep = header.map(() => '---')
  const body = norm.slice(1)
  const line = (cells) => `| ${cells.join(' | ')} |`
  return [line(header), line(sep), ...body.map(line)].join('\n')
}

export function downloadTextFile(filename, text, mime = 'text/plain;charset=utf-8') {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** Build an ``.xlsx`` from a matrix and trigger download (lazy-loads ``xlsx``). */
export async function downloadMatrixXlsx(matrix, filename = 'table.xlsx') {
  if (!matrix.length) return
  const XLSX = await import('xlsx')
  const sheet = XLSX.utils.aoa_to_sheet(matrix)
  const book = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(book, sheet, 'Sheet1')
  XLSX.writeFile(book, filename)
}

/** Toolbar + scroll wrapper around a rendered ``<table>…</table>`` fragment. */
export function wrapMdTableHtml(tableHtml) {
  return (
    `<div class="md-table-card" data-md-table>`
    + `<div class="md-table-toolbar" role="toolbar" aria-label="表格操作">`
    + `<button type="button" class="md-table-action" data-table-action="copy" title="复制表格" aria-label="复制表格">`
    + `<span class="material-symbols-outlined" aria-hidden="true">content_copy</span>`
    + `<span class="md-table-action-label">复制</span>`
    + `</button>`
    + `<button type="button" class="md-table-action" data-table-action="download" title="下载 Excel" aria-label="下载表格为 Excel">`
    + `<span class="material-symbols-outlined" aria-hidden="true">download</span>`
    + `<span class="md-table-action-label">下载</span>`
    + `</button>`
    + `</div>`
    + `<div class="md-table-scroll">${tableHtml}</div>`
    + `</div>`
  )
}
