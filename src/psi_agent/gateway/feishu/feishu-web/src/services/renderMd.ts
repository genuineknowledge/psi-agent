import { marked, Renderer } from "marked";
import katex from "katex";
import hljs from "highlight.js/lib/common";
import { wrapMdTableHtml } from "./mdTable";
import "katex/dist/katex.min.css";

marked.setOptions({ gfm: true, breaks: true });

export function htmlEscape(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/**
 * 无语言围栏的代码块走 ``highlightAuto`` 时的候选子集。
 *
 * 不传子集时它会遍历 ``highlight.js/lib/common`` 里全部 30+ 种语法, 每种都做一遍全文
 * 扫描 —— 这是解析里最贵的一步, 而流式期间每个 delta 都要重解析一遍整篇, 代价被放大成
 * O(n^2)。这里收窄到本产品实际会出现的语言: 仍然自动着色, 但扫描面小了一个量级。
 * 需要新语言时往这里加一条即可(不在子集里只影响着色, 不影响内容渲染)。
 */
const AUTO_LANGS = [
  "python",
  "javascript",
  "typescript",
  "json",
  "bash",
  "shell",
  "yaml",
  "sql",
  "xml",
  "css",
  "markdown",
  "diff",
];

function highlightCode(code: string, lang: string) {
  const language = hljs.getLanguage(lang) ? lang : null;
  try {
    const out = language
      ? hljs.highlight(code, { language, ignoreIllegals: true })
      : hljs.highlightAuto(code, AUTO_LANGS);
    return { html: out.value, language: out.language || language || "" };
  } catch {
    return { html: htmlEscape(code), language: "" };
  }
}

const markedRenderer = new Renderer();
markedRenderer.code = function ({ text, lang }: { text: string; lang?: string }) {
  const { html, language } = highlightCode(text, (lang || "").trim());
  const cls = language ? ` class="hljs language-${language}"` : ' class="hljs"';
  return `<pre><code${cls}>${html}</code></pre>\n`;
};
markedRenderer.table = function (token: {
  header: unknown[];
  rows: unknown[][];
}) {
  let header = "";
  for (let i = 0; i < token.header.length; i++) {
    header += this.tablecell(token.header[i] as never);
  }
  header = this.tablerow({ text: header } as never);
  let body = "";
  for (let i = 0; i < token.rows.length; i++) {
    const row = token.rows[i];
    let cells = "";
    for (let j = 0; j < row.length; j++) {
      cells += this.tablecell(row[j] as never);
    }
    body += this.tablerow({ text: cells } as never);
  }
  if (body) body = `<tbody>${body}</tbody>`;
  const tableHtml = `<table>\n<thead>\n${header}</thead>\n${body}</table>\n`;
  return wrapMdTableHtml(tableHtml);
};
markedRenderer.link = function (token: Parameters<Renderer["link"]>[0]) {
  const html = Renderer.prototype.link.call(this, token);
  if (!html.startsWith("<a ")) return html;
  return html.replace("<a ", '<a target="_blank" rel="noopener noreferrer" ');
};
marked.use({ renderer: markedRenderer });

const TABLE_ROW_RE = /^\s*\|.+\|\s*$/;
const TABLE_SEP_RE = /^\s*\|(?:\s*:?-+:?\s*\|)+\s*$/;

function isTableRow(line: string) {
  return TABLE_ROW_RE.test(line);
}

function isTableSeparator(line: string) {
  return TABLE_SEP_RE.test(line);
}

function isTableLine(line: string) {
  return isTableRow(line) || isTableSeparator(line);
}

function normalizeGfmTables(text: string) {
  const lines = text.split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    if (!isTableLine(lines[i])) {
      out.push(lines[i]);
      i++;
      continue;
    }
    const block: string[] = [];
    while (i < lines.length) {
      const cur = lines[i];
      if (cur.trim() === "") {
        let j = i + 1;
        while (j < lines.length && lines[j].trim() === "") j++;
        if (j < lines.length && isTableLine(lines[j])) {
          i++;
          continue;
        }
        break;
      }
      if (isTableLine(cur)) {
        block.push(cur.trim());
        i++;
        continue;
      }
      break;
    }
    out.push(...block);
  }
  return out.join("\n");
}

function unwrapFencedTables(text: string) {
  return text.replace(/```[^\n]*\n([\s\S]*?)```/g, (full, inner: string) => {
    const body = inner.trim();
    if (!body) return full;
    const innerLines = body.split("\n");
    if (innerLines.length >= 2 && innerLines.every(isTableLine)) return body;
    return full;
  });
}

/** Render assistant Markdown (GFM tables, KaTeX, code highlight) - spa v1 parity. */
export function renderMd(text: string): string {
  const macros: { block: boolean; tex: string }[] = [];
  const normalized = normalizeGfmTables(unwrapFencedTables(text));
  const s = normalized
    .replace(/\$\$([\s\S]+?)\$\$/g, (_m: string, body: string) => {
      const i = macros.length;
      macros.push({ block: true, tex: body.trim() });
      return `\x00MATH${i}\x00`;
    })
    .replace(/\$([^$]+?)\$/g, (_m: string, body: string) => {
      const i = macros.length;
      macros.push({ block: false, tex: body.trim() });
      return `\x00MATH${i}\x00`;
    });
  let html = String(marked.parse(s));
  macros.forEach((m, i) => {
    try {
      const rendered = katex.renderToString(m.tex, { displayMode: m.block, throwOnError: false });
      html = html.replace(`\x00MATH${i}\x00`, rendered);
    } catch {
      html = html.replace(`\x00MATH${i}\x00`, `<code>${htmlEscape(m.tex)}</code>`);
    }
  });
  return html;
}
