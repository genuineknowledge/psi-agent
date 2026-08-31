import type { ChatEvent } from "./api";

/** One chartable series: a label column plus one or more numeric columns. */
export interface ChartItem {
  label: string;
  values: number[];
}

export interface ChartSeries {
  /** The label column name. */
  label: string;
  /** Numeric column names, aligned with ChartItem.values. */
  metrics: string[];
  items: ChartItem[];
  /** True when the label column reads as time (期次/月份/季度/年份). */
  isTimeSeries: boolean;
  /** Recommended chart kind — the viewer can override in the UI. */
  suggest: "pie" | "bars" | "line";
}

const TOOL_RESULT_PREFIX = "[Tool Result: ";

function parseToolResult(event: ChatEvent): unknown {
  if (event.type !== "reasoning" || event.kind !== "tool_result") return null;
  const text = event.text ?? "";
  if (!text.startsWith(TOOL_RESULT_PREFIX)) return null;
  const body = text.slice(TOOL_RESULT_PREFIX.length, text.lastIndexOf("]"));
  try {
    return JSON.parse(body);
  } catch {
    return null;
  }
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const cleaned = value.replace(/,/g, "").replace(/%$/, "");
    // Date-ish labels (2026-08, 2025/01) are not numbers — the leading-prefix
    // extraction below must not turn "2026-08" into 2026.
    if (/^\d{4}[-/]/.test(cleaned)) return null;
    // Real answers carry annotated numbers: "0(未生成)", "14 条",
    // "5.3% (1/19)" — take the leading numeric prefix when it stands alone
    // or is followed by a parenthesis or whitespace (a note/unit). A range
    // like "30-90天" (digit followed by a dash) is a LABEL, not a value.
    const match = /^-?\d+(?:\.\d+)?(?:\(|\s|$)/.exec(cleaned);
    if (match) {
      const parsed = Number(match[0]);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

/** Columns that order rows, not measure them — never chartable metrics. */
const RANK_COLUMNS = /rank|排名|名次|序号|顺位|\bno\b|^#/i;
/** Identifier/code columns (id, task_no, code, 编号, 代码) — inventory keys,
 * not quantities. Charting them produced nonsense bars (3 bars per label). */
const IDENTITY_COLUMNS = /(^|_|\s)(id|no|code)(_|\s|$)|编号|代码/i;
/** Bucket columns (freshness_bucket, bucket, 分档) are labels, even when the
 * bucket happens to be a number-like string ("2025"). */
const BUCKET_COLUMNS = /bucket|分档|档位/i;
/** A bare status column is a code (0/1/2/3) — its readable twin is
 * status_label. The code itself charted as a metric before. */
const STATUS_CODE_COLUMNS = /^status$/i;
/** Row labels that aggregate the table itself — excluded from any series. */
const SUMMARY_LABELS = /^(合计|总计|小计|平均|—|-|－|共)$/;
/** Share/rate metrics deserve round charts. */
const RATE_METRICS = /rate|pct|percent|完成率|占比|比例/i;
/** Status-ish labels: the plan's own example (在推进/阻塞/已完成) is a share pie. */
const STATUS_WORDS = /未开始|进行中|已完成|已停用|阻塞|在办|待办|推进|已发布|未发布|在途|滞后|逾期|正常/;
/** Time-ish labels: 期次 / 月份 / 季度 / 年份 — these want a line chart. */
const TIME_WORDS = /第\d+期|\d{1,2}月|Q[1-4]|\d{4}年?|\d{4}-\d{1,2}/;

/**
 * Pull a chartable series out of the streamed tool results (P0-3/P1-2).
 *
 * The LAST chartable tool result wins, not the first: the model queries
 * several tools per answer and the final table it surfaces is the one it
 * considered most relevant to the question. Taking the first used to chart a
 * random intermediate query (schema dumps, health rows), so the same question
 * produced different charts on different runs.
 */
export function extractChartData(evidence: ChatEvent[]): ChartSeries | null {
  let best: ChartSeries | null = null;
  for (const event of evidence) {
    const payload = parseToolResult(event);
    if (!payload || typeof payload !== "object") continue;
    const { columns, rows } = payload as { columns?: unknown; rows?: unknown };
    if (!Array.isArray(columns) || !Array.isArray(rows) || rows.length < 2) continue;
    const series = seriesFromColumns(
      columns.filter((c): c is string => typeof c === "string"),
      rows as Record<string, unknown>[],
    );
    if (series) best = series;
  }
  return best;
}

/**
 * Chart from the answer's own markdown tables (covers turns where the model
 * restates a table from history without calling a tool).
 */
export function extractChartDataFromMarkdown(markdown: string): ChartSeries | null {
  for (const table of markdownTables(markdown)) {
    const rows = table.rows;
    if (rows.length < 2) continue;
    const series = seriesFromColumns(table.headers, rows);
    if (series) return series;
  }
  return null;
}

function markdownTables(markdown: string): { headers: string[]; rows: Record<string, unknown>[] }[] {
  const tables: { headers: string[]; rows: Record<string, unknown>[] }[] = [];
  const lines = markdown.split("\n");
  let index = 0;
  while (index < lines.length) {
    const headerLine = lines[index].trim();
    const separatorLine = lines[index + 1]?.trim() ?? "";
    if (
      !headerLine.startsWith("|") ||
      !/^\|?[\s:|-]+\|?$/.test(separatorLine) ||
      !separatorLine.includes("-")
    ) {
      index += 1;
      continue;
    }
    const headers = splitRow(headerLine);
    const rows: Record<string, unknown>[] = [];
    index += 2;
    while (index < lines.length && lines[index].trim().startsWith("|")) {
      const cells = splitRow(lines[index]);
      if (cells.length) {
        const row: Record<string, unknown> = {};
        // Indexed keys keep duplicate headers intact (side-by-side column
        // pairs); splitDuplicateColumns collapses them afterwards.
        headers.forEach((header, columnIndex) => {
          row[`${header}#${columnIndex}`] = cells[columnIndex]?.trim() ?? "";
        });
        rows.push(row);
      }
      index += 1;
    }
    if (headers.length && rows.length) {
      // Models sometimes fold a long series into side-by-side column pairs
      // (| 月份 | 上报数 | 月份 | 上报数 |). Split each such table back into
      // per-period tables so duplicate headers do not overwrite each other.
      tables.push(...splitDuplicateColumns({ headers, rows }));
    }
  }
  return tables;
}

function splitDuplicateColumns(table: {
  headers: string[];
  rows: Record<string, unknown>[];
}): { headers: string[]; rows: Record<string, unknown>[] }[] {
  const { headers, rows } = table;
  const seen = new Set<string>();
  let period = headers.length;
  for (const header of headers) {
    if (seen.has(header)) {
      period = seen.size;
      break;
    }
    seen.add(header);
  }
  if (period >= headers.length) {
    // No duplicates: collapse indexed keys back to plain header keys.
    const plain = {
      headers,
      rows: rows.map((row) => {
        const result: Record<string, unknown> = {};
        headers.forEach((header, index) => {
          result[header] = row[`${header}#${index}`] ?? "";
        });
        return result;
      }),
    };
    return [plain];
  }

  // Every segment shares the same header names: concatenate the rows into one
  // long table (left pair rows first, then right pair rows).
  const mergedRows: Record<string, unknown>[] = [];
  const segmentHeaders = headers.slice(0, period);
  for (let start = 0; start < headers.length; start += period) {
    for (const row of rows) {
      const partRow: Record<string, unknown> = {};
      let empty = true;
      segmentHeaders.forEach((header, offset) => {
        const value = row[`${header}#${start + offset}`] ?? "";
        partRow[header] = value;
        if (String(value).trim() !== "") empty = false;
      });
      if (!empty) mergedRows.push(partRow);
    }
  }
  return mergedRows.length ? [{ headers: segmentHeaders, rows: mergedRows }] : [table];
}

function splitRow(line: string): string[] {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|")) text = text.slice(0, -1);
  return text.split("|").map(stripMarkdown);
}

/** Strip inline markdown decorators ("**合计**" → "合计") before parsing. */
function stripMarkdown(value: string): string {
  return value.replace(/\*\*/g, "").replace(/`/g, "").trim();
}

function seriesFromColumns(columns: string[], rows: Record<string, unknown>[]): ChartSeries | null {
  const dataRows = rows.filter((row) => !SUMMARY_LABELS.test(String(row[columns[0]] ?? "").trim()));
  if (dataRows.length < 2) return null;
  // Label columns are non-empty strings that are NOT numbers in disguise:
  // "10" is a metric, "30-90天" is a label. Bucket columns are labels by
  // name whatever their type — a year bucket may arrive as the number 2025.
  const labels = columns.filter((column) =>
    dataRows.every((row) => {
      if (BUCKET_COLUMNS.test(column)) return true;
      const value = row[column];
      return typeof value === "string" && String(value).trim() !== "" && toNumber(value) === null;
    }),
  );
  const metrics = columns.filter(
    (column) =>
      !RANK_COLUMNS.test(column) &&
      !IDENTITY_COLUMNS.test(column) &&
      !BUCKET_COLUMNS.test(column) &&
      !STATUS_CODE_COLUMNS.test(column) &&
      dataRows.every((row) => toNumber(row[column]) !== null),
  );
  // A chart table has exactly one label column. Two or more string columns
  // (task lists carry name + category + owner…) means an inventory table,
  // not a dimension/measure pair — no chart.
  if (labels.length !== 1 || !metrics.length) return null;

  const labelColumn = labels[0];
  const isTimeSeries = dataRows.every((row) => TIME_WORDS.test(String(row[labelColumn])));
  const statusLike = !isTimeSeries && dataRows.every((row) => STATUS_WORDS.test(String(row[labelColumn])));
  // Prefer a rate/percent metric first for round charts; keep column order otherwise.
  const orderedMetrics = [...metrics].sort((a, b) => {
    const aRate = RATE_METRICS.test(a) ? 1 : 0;
    const bRate = RATE_METRICS.test(b) ? 1 : 0;
    return bRate - aRate;
  });

  return {
    label: labelColumn,
    metrics: orderedMetrics,
    items: dataRows.map((row) => ({
      label: String(row[labelColumn]),
      values: orderedMetrics.map((metric) => toNumber(row[metric]) ?? 0),
    })),
    isTimeSeries,
    // 需求 P0-3 的例子是「占比 → 饼图」;P1-2 加时间趋势 → 折线。
    suggest: isTimeSeries ? "line" : statusLike || RATE_METRICS.test(orderedMetrics[0]) ? "pie" : "bars",
  };
}
