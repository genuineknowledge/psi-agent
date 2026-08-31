import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** One markdown table parsed into plain cells. */
export interface MarkdownTable {
  headers: string[];
  rows: string[][];
}

interface MarkdownPart {
  kind: "text" | "table";
  text: string;
  table?: MarkdownTable;
}

function stripDecorators(value: string): string {
  return value.replace(/\*\*/g, "").replace(/`/g, "").trim();
}

function splitCells(line: string): string[] {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|")) text = text.slice(0, -1);
  return text.split("|").map((cell) => cell.trim());
}

/** Split markdown into alternating text / table parts. Tables go to the
 * sortable renderer (plan 6.2); everything else stays ReactMarkdown's. */
export function splitMarkdownParts(content: string): MarkdownPart[] {
  const lines = content.split("\n");
  const parts: MarkdownPart[] = [];
  let textBuffer: string[] = [];
  let index = 0;
  const flushText = () => {
    if (textBuffer.length) {
      parts.push({ kind: "text", text: textBuffer.join("\n") });
      textBuffer = [];
    }
  };
  while (index < lines.length) {
    const headerLine = lines[index].trim();
    const separatorLine = lines[index + 1]?.trim() ?? "";
    if (
      headerLine.startsWith("|") &&
      /^\|?[\s:|-]+\|?$/.test(separatorLine) &&
      separatorLine.includes("-")
    ) {
      flushText();
      const headers = splitCells(headerLine);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        rows.push(splitCells(lines[index]));
        index += 1;
      }
      parts.push({ kind: "table", text: "", table: { headers, rows } });
    } else {
      textBuffer.push(lines[index]);
      index += 1;
    }
  }
  flushText();
  return parts;
}

function toNumberOrNull(value: string): number | null {
  const cleaned = value.replace(/,/g, "").replace(/%/g, "").trim();
  if (!cleaned || /^\d{4}[-/]/.test(cleaned)) return null;
  const match = /^-?\d+(?:\.\d+)?/.exec(cleaned);
  return match ? Number(match[0]) : null;
}

/** A markdown table with clickable sortable headers (plan 6.2: 可排序表格).
 *
 * A column sorts numerically when every cell parses as a number (percent and
 * thousand separators tolerated); otherwise it sorts as text.
 */
export function SortableTable({ table }: { table: MarkdownTable }) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);

  const sortedRows = useMemo(() => {
    if (!sort) return table.rows;
    const column = sort.col;
    const numeric =
      table.rows.length > 0 && table.rows.every((row) => toNumberOrNull(row[column] ?? "") !== null);
    const keyOf = (row: string[]): number | string => {
      if (numeric) return toNumberOrNull(row[column] ?? "") ?? 0;
      return row[column] ?? "";
    };
    return [...table.rows].sort((a, b) => {
      const keyA = keyOf(a);
      const keyB = keyOf(b);
      if (typeof keyA === "number" && typeof keyB === "number") {
        if (keyA < keyB) return -sort.dir;
        if (keyA > keyB) return sort.dir;
        return 0;
      }
      // Text columns sort by pinyin (zh-Hans-CN), not by raw code points —
      // Unicode order is meaningless to a reader.
      return String(keyA).localeCompare(String(keyB), "zh-Hans-CN") * sort.dir;
    });
  }, [table, sort]);

  return (
    <table>
      <thead>
        <tr>
          {table.headers.map((header, index) => (
            <th
              key={index}
              className="sortableTh"
              title="点击排序"
              onClick={() =>
                setSort((current) =>
                  current?.col === index ? { col: index, dir: current.dir === 1 ? -1 : 1 } : { col: index, dir: 1 },
                )
              }
            >
              {stripDecorators(header)}
              {/* The mark is always rendered (faint ⇅ = sortable) and keeps a
                  fixed width, so sorting never shifts the column layout. */}
              <span className={`sortMark${sort?.col === index ? " active" : ""}`}>
                {sort?.col === index ? (sort.dir === 1 ? "▲" : "▼") : "⇅"}
              </span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sortedRows.map((row, rowIndex) => (
          <tr key={rowIndex}>
            {row.map((cell, cellIndex) => (
              <td key={cellIndex}>{stripDecorators(cell)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Answer-body renderer: plain text via ReactMarkdown, tables sortable. */
export function MarkdownWithSortableTables({ content }: { content: string }) {
  const parts = useMemo(() => splitMarkdownParts(content), [content]);
  return (
    <>
      {parts.map((part, index) =>
        part.kind === "text" ? (
          <ReactMarkdown key={index} remarkPlugins={[remarkGfm]}>
            {part.text}
          </ReactMarkdown>
        ) : (
          <SortableTable key={index} table={part.table as MarkdownTable} />
        ),
      )}
    </>
  );
}
