import { describe, expect, it } from "vitest";
import { extractChartData, extractChartDataFromMarkdown } from "./chart-data";
import type { ChatEvent } from "./api";

function toolResult(text: string): ChatEvent {
  return { type: "reasoning", kind: "tool_result", text: `[Tool Result: ${text}]` };
}

describe("extractChartData", () => {
  it("extracts a label + numeric series from a tool result", () => {
    const evidence = [
      toolResult(
        JSON.stringify({
          ok: true,
          columns: ["group_name", "cnt"],
          rows: [
            { group_name: "标准安全组", cnt: 19 },
            { group_name: "市场化改革组", cnt: 15 },
          ],
        }),
      ),
    ];
    const series = extractChartData(evidence);
    expect(series).not.toBeNull();
    expect(series?.metrics).toEqual(["cnt"]);
    expect(series?.items).toEqual([
      { label: "标准安全组", values: [19] },
      { label: "市场化改革组", values: [15] },
    ]);
  });

  it("coerces string numbers (tool results carry some metrics as strings)", () => {
    const evidence = [
      toolResult(
        JSON.stringify({
          columns: ["group_name", "finish_rate_pct"],
          rows: [
            { group_name: "A", finish_rate_pct: "5.3" },
            { group_name: "B", finish_rate_pct: "33.3" },
          ],
        }),
      ),
    ];
    const series = extractChartData(evidence);
    expect(series?.metrics[0]).toBe("finish_rate_pct");
    expect(series?.items[0].values[0]).toBe(5.3);
  });

  it("prefers a rate/percent metric first", () => {
    const evidence = [
      toolResult(
        JSON.stringify({
          columns: ["group_name", "cnt", "finish_rate_pct"],
          rows: [
            { group_name: "A", cnt: 10, finish_rate_pct: "20" },
            { group_name: "B", cnt: 40, finish_rate_pct: "60" },
          ],
        }),
      ),
    ];
    expect(extractChartData(evidence)?.metrics[0]).toBe("finish_rate_pct");
  });

  it("returns null for a single row (nothing to draw)", () => {
    const evidence = [
      toolResult(JSON.stringify({ columns: ["name", "cnt"], rows: [{ name: "A", cnt: 1 }] })),
    ];
    expect(extractChartData(evidence)).toBeNull();
  });

  it("returns null when no numeric column exists", () => {
    const evidence = [
      toolResult(
        JSON.stringify({
          columns: ["name", "note"],
          rows: [
            { name: "A", note: "x" },
            { name: "B", note: "y" },
          ],
        }),
      ),
    ];
    expect(extractChartData(evidence)).toBeNull();
  });

  it("ignores non-tool-result events and malformed JSON", () => {
    const evidence: ChatEvent[] = [
      { type: "reasoning", kind: "thinking", text: "let me think" },
      toolResult("not json"),
      { type: "text", text: "plain answer" },
    ];
    expect(extractChartData(evidence)).toBeNull();
  });

  it("charts a markdown table from the answer text", () => {
    const markdown = [
      "## 看板分布",
      "",
      "| 看板 | 任务数 |",
      "| --- | --- |",
      "| 技术组重点任务进展 | 82 |",
      "| 集团重点任务调度 | 46 |",
    ].join("\n");
    const series = extractChartDataFromMarkdown(markdown);
    expect(series).not.toBeNull();
    expect(series?.label).toBe("看板");
    expect(series?.metrics).toEqual(["任务数"]);
    expect(series?.items).toEqual([
      { label: "技术组重点任务进展", values: [82] },
      { label: "集团重点任务调度", values: [46] },
    ]);
  });

  it("picks a rate column from a markdown table for the donut", () => {
    const markdown = [
      "| 专项组 | 任务数 | 完成率 |",
      "| --- | --- | --- |",
      "| A组 | 10 | 20.0% |",
      "| B组 | 40 | 60.0% |",
    ].join("\n");
    expect(extractChartDataFromMarkdown(markdown)?.metrics[0]).toBe("完成率");
  });

  it("returns null for prose without a table", () => {
    expect(extractChartDataFromMarkdown("只有一段文字,没有表格。")).toBeNull();
  });

  it("strips bold decorators in cells (the real answer bolds the 合计 row)", () => {
    const markdown = [
      "| 专项组 | 任务数 |",
      "| --- | --- |",
      "| 标准安全组 | 19 |",
      "| 国家工程办 | 15 |",
      "| **合计** | **128** |",
    ].join("\n");
    const series = extractChartDataFromMarkdown(markdown);
    expect(series).not.toBeNull();
    expect(series?.items).toHaveLength(2);
    expect(series?.items.some((item) => item.label === "合计")).toBe(false);
  });

  it("suggests a pie for status distributions (the plan's P0-3 example)", () => {
    const markdown = [
      "| 状态 | 任务数 |",
      "| --- | --- |",
      "| 未开始 | 14 |",
      "| 进行中 | 78 |",
      "| 已完成 | 31 |",
      "| 已停用 | 5 |",
    ].join("\n");
    expect(extractChartDataFromMarkdown(markdown)?.suggest).toBe("pie");
  });

  it("suggests bars for plain counts without status words", () => {
    const markdown = [
      "| 专项组 | 任务数 |",
      "| --- | --- |",
      "| 标准安全组 | 19 |",
      "| 国家工程办 | 15 |",
    ].join("\n");
    expect(extractChartDataFromMarkdown(markdown)?.suggest).toBe("bars");
  });

  it("parses annotated numbers like 0(未生成)", () => {
    const markdown = [
      "| 月份 | 上报数 |",
      "| --- | --- |",
      "| 2026-07 | 17 |",
      "| 2026-08 | 0（未生成） |",
    ].join("\n");
    const series = extractChartDataFromMarkdown(markdown);
    expect(series?.suggest).toBe("line");
    expect(series?.items[1].values[0]).toBe(0);
  });

  it("suggests a line chart for time-series labels (P1-2)", () => {
    const markdown = [
      "| 月份 | 上报数 |",
      "| --- | --- |",
      "| 3月 | 10 |",
      "| 4月 | 22 |",
      "| 5月 | 31 |",
    ].join("\n");
    const series = extractChartDataFromMarkdown(markdown);
    expect(series?.suggest).toBe("line");
    expect(series?.isTimeSeries).toBe(true);
  });

  it("keeps multiple metrics for grouped comparison (P1-2)", () => {
    const markdown = [
      "| 专项组 | 任务数 | 已完成 |",
      "| --- | --- | --- |",
      "| 标准安全组 | 19 | 1 |",
      "| 国家工程办 | 15 | 2 |",
    ].join("\n");
    const series = extractChartDataFromMarkdown(markdown);
    expect(series?.metrics).toEqual(["任务数", "已完成"]);
    expect(series?.items[0].values).toEqual([19, 1]);
  });

  it("splits side-by-side duplicate column pairs (the real trend table)", () => {
    const markdown = [
      "| 月份 | 上报数 | 月份 | 上报数 |",
      "| --- | --- | --- | --- |",
      "| 2025-01 | 27 | 2025-11 | 59 |",
      "| 2025-02 | 33 | 2025-12 | 58 |",
      "| 2025-03 | 37 | | |",
    ].join("\n");
    const series = extractChartDataFromMarkdown(markdown);
    expect(series).not.toBeNull();
    expect(series?.isTimeSeries).toBe(true);
    expect(series?.suggest).toBe("line");
    // Left pair + right pair (empty right cells dropped), concatenated.
    expect(series?.items).toEqual([
      { label: "2025-01", values: [27] },
      { label: "2025-02", values: [33] },
      { label: "2025-03", values: [37] },
      { label: "2025-11", values: [59] },
      { label: "2025-12", values: [58] },
    ]);
  });

  it("ignores rank columns and summary rows (ranking table with 合计)", () => {
    const markdown = [
      "| 排名 | 专项组 | 任务数 |",
      "| --- | --- | --- |",
      "| 1 | 标准安全组 | 19 |",
      "| 2 | 国家工程办 | 15 |",
      "| 2 | 数据基础设施组 | 15 |",
      "| 合计 | — | 128 |",
    ].join("\n");
    const series = extractChartDataFromMarkdown(markdown);
    expect(series?.metrics).toEqual(["任务数"]);
    expect(series?.items).toHaveLength(3);
    expect(series?.items.some((item) => item.label === "合计")).toBe(false);
  });
});
