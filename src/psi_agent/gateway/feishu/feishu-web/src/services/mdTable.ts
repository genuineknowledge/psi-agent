function cellText(cell: Element | null): string {
  return (cell?.textContent || "").replace(/\u00a0/g, " ").trim();
}

export function tableToMatrix(table: HTMLTableElement | null | undefined): string[][] {
  if (!table) return [];
  const rows: string[][] = [];
  table.querySelectorAll("tr").forEach((tr) => {
    const cells = [...tr.querySelectorAll("th, td")].map(cellText);
    if (cells.length) rows.push(cells);
  });
  return rows;
}

function escapeTsvCell(value: unknown): string {
  const s = String(value ?? "");
  if (/[\t\n\r"]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

export function matrixToTsv(matrix: string[][]): string {
  return matrix.map((row) => row.map(escapeTsvCell).join("\t")).join("\n");
}

export function downloadTextFile(filename: string, text: string, mime = "text/plain;charset=utf-8") {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadMatrixTable(matrix: string[][], filename = "table.tsv") {
  if (!matrix.length) return;
  downloadTextFile(filename, matrixToTsv(matrix), "text/tab-separated-values;charset=utf-8");
}

/** Toolbar + scroll wrapper around a rendered <table> fragment. */
export function wrapMdTableHtml(tableHtml: string): string {
  const copyIcon =
    `<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>`;
  const downloadIcon =
    `<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>`;
  return (
    `<div class="md-table-card" data-md-table>`
    + `<div class="md-table-toolbar" role="toolbar" aria-label="表格操作">`
    + `<button type="button" class="md-table-action" data-table-action="copy" title="复制表格" aria-label="复制表格">`
    + copyIcon
    + `</button>`
    + `<button type="button" class="md-table-action" data-table-action="download" title="下载表格" aria-label="下载表格">`
    + downloadIcon
    + `</button>`
    + `</div>`
    + `<div class="md-table-scroll">${tableHtml}</div>`
    + `</div>`
  );
}

/** Wrap bare <table> elements produced by markdown rendering. */
export function decorateMarkdownTables(html: string): string {
  const tableRe = /<table>([\s\S]*?)<\/table>/gi;
  return html.replace(tableRe, (_whole, body: string) => wrapMdTableHtml(`<table>${body}</table>`));
}
