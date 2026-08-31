import { useState } from "react";
import type { ChartSeries } from "../chart-data";

type ChartKind = "pie" | "bars" | "line";

const PALETTE = ["#0f6b54", "#39a57c", "#7fc4a8", "#0b4f3d", "#69b894", "#2d8a6a", "#9fd3bd", "#1b7a62"];

/**
 * Dependency-free SVG charts for P0-3 / P1-2 visualisation.
 *
 * - pie:  status / share data (the plan's own example)
 * - bars: plain counts; grouped bars when several metrics share one label
 * - line: time series (期次 / 月份 / 季度 labels)
 *
 * A small toggle lets the viewer override the guess, and the current chart
 * can be downloaded as PNG. The download renders a *complete* standalone
 * SVG (chart + title + legend + values) from the data — it does not grab the
 * on-screen DOM, so every kind downloads identically well.
 */
export function DataChart({ series }: { series: ChartSeries }) {
  const [kind, setKind] = useState<ChartKind>(series.suggest);
  const total = series.items.reduce((sum, item) => sum + (item.values[0] ?? 0), 0);

  function downloadPng() {
    const svgText = buildDownloadSvg(series, kind);
    const blob = new Blob([svgText], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
      const match = /viewBox="([\d. ]+)"/.exec(svgText);
      const box = match ? match[1].split(" ").map(Number) : [0, 0, 640, 320];
      const scale = 2; // 2x for crisp export
      const canvas = document.createElement("canvas");
      canvas.width = box[2] * scale;
      canvas.height = box[3] * scale;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      const link = document.createElement("a");
      link.href = canvas.toDataURL("image/png");
      const kindName = kind === "pie" ? "饼图" : kind === "bars" ? "条形图" : "折线图";
      link.download = `${series.label}-${series.metrics.join("与")}-${kindName}.png`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    };
    image.src = url;
  }

  return (
    <div className="dataChart">
      <div className="dataChartHead">
        <div className="dataChartTitle">
          数据图表 · {series.metrics.join(" / ")}
          {total > 0 && kind === "pie" && `(合计 ${total})`}
        </div>
        <div className="dataChartTools">
          <div className="dataChartKinds">
            {(["pie", "bars", "line"] as ChartKind[]).map((option) => (
              <button key={option} className={kind === option ? "active" : ""} onClick={() => setKind(option)}>
                {option === "pie" ? "饼图" : option === "bars" ? "条形" : "折线"}
              </button>
            ))}
          </div>
          <button className="downloadChart" onClick={downloadPng} title="下载为 PNG(含图例与数值)">
            下载
          </button>
        </div>
      </div>
      {kind === "pie" && <PieChart series={series} />}
      {kind === "bars" && <BarsChart series={series} />}
      {kind === "line" && <LineChart series={series} />}
    </div>
  );
}

/** Solid pie from the first metric, segments from 12 o'clock. */
function PieChart({ series }: { series: ChartSeries }) {
  const values = series.items.map((item) => item.values[0] ?? 0);
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total <= 0) return null;
  const radius = 64;
  const center = 80;
  let angle = -Math.PI / 2;

  function arc(fraction: number): string {
    const end = angle + fraction * Math.PI * 2;
    const largeArc = fraction > 0.5 ? 1 : 0;
    const x1 = center + radius * Math.cos(angle);
    const y1 = center + radius * Math.sin(angle);
    const x2 = center + radius * Math.cos(end);
    const y2 = center + radius * Math.sin(end);
    angle = end;
    return `M ${center} ${center} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`;
  }

  return (
    <div className="dataChartDonut">
      <svg viewBox="0 0 160 160" role="img" aria-label="饼图">
        {series.items.map((item, index) => (
          <path key={item.label} d={arc((item.values[0] ?? 0) / total)} fill={PALETTE[index % PALETTE.length]} />
        ))}
      </svg>
      {/* Few entries read best as one column; beyond ~6-7 the legend folds
          into as many columns as the card width allows (same threshold as
          the downloaded PNG's two-column fold). */}
      <div className={`dataChartLegend${series.items.length > 6 ? " folded" : ""}`}>
        {series.items.map((item, index) => (
          <div className="dataLegendRow" key={item.label}>
            <i style={{ background: PALETTE[index % PALETTE.length] }} />
            <span>{item.label}</span>
            <b>{item.values[0]}</b>
            <small>{`${(((item.values[0] ?? 0) / total) * 100).toFixed(1)}%`}</small>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Horizontal bars (single metric) or grouped vertical bars (several). */
function BarsChart({ series }: { series: ChartSeries }) {
  const single = series.metrics.length === 1;
  if (single) {
    const values = series.items.map((item) => item.values[0] ?? 0);
    const max = Math.max(...values, 1);
    return (
      <div className="dataChartBars">
        {series.items.map((item) => (
          <div className="dataBarRow" key={item.label}>
            <span className="dataBarLabel">{item.label}</span>
            <span className="dataBarTrack">
              <span className="dataBarFill" style={{ width: `${Math.max(((item.values[0] ?? 0) / max) * 100, 1.5)}%` }} />
            </span>
            <span className="dataBarValue">{item.values[0]}</span>
          </div>
        ))}
      </div>
    );
  }
  return <GroupedBars series={series} />;
}

const GROUP_WIDTH = 640;
const GROUP_HEIGHT = 280;

/** Vertical grouped bars: one group per item, one bar per metric. */
function GroupedBars({ series }: { series: ChartSeries }) {
  const all = series.items.flatMap((item) => item.values);
  const max = Math.max(...all, 1);
  const width = GROUP_WIDTH;
  const height = GROUP_HEIGHT;
  const padLeft = 44;
  const padBottom = 44;
  const plotWidth = width - padLeft - 12;
  const plotHeight = height - 28 - padBottom;
  const groupSlot = plotWidth / series.items.length;
  const barSlot = groupSlot / Math.max(series.metrics.length, 1);
  const barWidth = Math.min(barSlot * 0.62, 26);

  return (
    <div className="groupedBars">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="分组条形图">
        {[0.25, 0.5, 0.75, 1].map((fraction) => {
          const y = 28 + plotHeight * (1 - fraction);
          return (
            <g key={fraction}>
              <line x1={padLeft} y1={y} x2={width - 12} y2={y} stroke="#eef1ef" strokeWidth={1} />
              <text x={padLeft - 8} y={y + 4} textAnchor="end" fontSize={10} fill="#8e9995">
                {Math.round(max * fraction)}
              </text>
            </g>
          );
        })}
        {series.items.map((item, groupIndex) => {
          const groupX = padLeft + groupIndex * groupSlot;
          return (
            <g key={item.label}>
              {item.values.map((value, metricIndex) => {
                const barHeight = (value / max) * plotHeight;
                const x = groupX + metricIndex * barSlot + (barSlot - barWidth) / 2;
                const y = 28 + plotHeight - barHeight;
                return (
                  <rect
                    key={metricIndex}
                    x={x}
                    y={y}
                    width={barWidth}
                    height={barHeight}
                    rx={3}
                    fill={PALETTE[metricIndex % PALETTE.length]}
                  />
                );
              })}
              <text x={groupX + groupSlot / 2} y={height - padBottom + 16} textAnchor="middle" fontSize={10} fill="#4f5a56">
                {item.label.length > 7 ? `${item.label.slice(0, 7)}…` : item.label}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="dataChartLegend">
        {series.metrics.map((name, index) => (
          <div className="dataLegendRow" key={name}>
            <i style={{ background: PALETTE[index % PALETTE.length] }} />
            <span>{name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const LINE_WIDTH = 560;
const LINE_HEIGHT = 260;

/** Multi-series line chart for time labels. */
function LineChart({ series }: { series: ChartSeries }) {
  const width = LINE_WIDTH;
  const height = LINE_HEIGHT;
  const padLeft = 40;
  const padBottom = 30;
  const plotWidth = width - padLeft - 14;
  const plotHeight = height - 26 - padBottom;
  const max = Math.max(...series.items.flatMap((item) => item.values), 1);

  function pointFor(index: number, value: number): [number, number] {
    const x = series.items.length === 1 ? padLeft + plotWidth / 2 : padLeft + (index / (series.items.length - 1)) * plotWidth;
    const y = 26 + plotHeight * (1 - value / max);
    return [x, y];
  }

  return (
    <div className="lineChart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="折线图">
        {[0.25, 0.5, 0.75, 1].map((fraction) => {
          const y = 26 + plotHeight * (1 - fraction);
          return (
            <g key={fraction}>
              <line x1={padLeft} y1={y} x2={width - 14} y2={y} stroke="#eef1ef" strokeWidth={1} />
              <text x={padLeft - 8} y={y + 4} textAnchor="end" fontSize={10} fill="#8e9995">
                {Math.round(max * fraction)}
              </text>
            </g>
          );
        })}
        {series.metrics.map((name, metricIndex) => {
          const points = series.items
            .map((item, index) => pointFor(index, item.values[metricIndex] ?? 0))
            .map(([x, y]) => `${x},${y}`)
            .join(" ");
          const color = PALETTE[metricIndex % PALETTE.length];
          return (
            <g key={name}>
              <polyline points={points} fill="none" stroke={color} strokeWidth={2.2} strokeLinejoin="round" />
              {series.items.map((item, index) => {
                const [x, y] = pointFor(index, item.values[metricIndex] ?? 0);
                return <circle key={index} cx={x} cy={y} r={3.2} fill="#fff" stroke={color} strokeWidth={2} />;
              })}
            </g>
          );
        })}
        {series.items.map((item, index) => {
          // Thin the x labels only when they would crowd: up to 12 items
          // every label shows. Beyond that, keep the first, the last, and
          // every step-th label in between — but never one that would sit
          // right next to the last label.
          const last = series.items.length - 1;
          const step = Math.ceil(series.items.length / 12);
          // Middle labels keep at least `step` slots clear of BOTH edges —
          // hugging the first or last label was how neighbours overlapped.
          const showLabel =
            series.items.length <= 12 ||
            index === 0 ||
            index === last ||
            (index % step === 0 && index >= step * 2 && index + step <= last);
          if (!showLabel) return null;
          const [x] = pointFor(index, 0);
          // First label left-anchored, last label right-anchored — a centered
          // label at either edge gets clipped by the SVG boundary.
          const anchor = index === 0 ? "start" : index === last ? "end" : "middle";
          return (
            <text key={item.label} x={x} y={height - 8} textAnchor={anchor} fontSize={10} fill="#4f5a56">
              {item.label}
            </text>
          );
        })}
      </svg>
      <div className="dataChartLegend">
        {series.metrics.map((name, index) => (
          <div className="dataLegendRow" key={name}>
            <i style={{ background: PALETTE[index % PALETTE.length] }} />
            <span>{name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Download rendering: a complete standalone SVG per kind ─────────────────

function svg(viewBox: [number, number, number, number], body: string): string {
  // width/height attributes are load-bearing: an SVG blob without them gets
  // an intrinsic size of 300×150 in the browser, and drawImage then stretches
  // the artwork — the earlier "squashed pie" export bug.
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${viewBox[2]}" height="${viewBox[3]}" viewBox="${viewBox.join(" ")}">` +
    `<rect x="0" y="0" width="${viewBox[2]}" height="${viewBox[3]}" fill="#ffffff"/>` +
    `<text x="24" y="34" font-size="18" font-weight="600" fill="#15201d" font-family="Microsoft YaHei, PingFang SC, sans-serif">${escapeXml("数据图表")}</text>` +
    body +
    `</svg>`
  );
}

function escapeXml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function legendBlock(x: number, y: number, entries: { color: string; text: string }[], rowHeight: number): string {
  return entries
    .map((entry, index) => {
      const rowY = y + index * rowHeight;
      return (
        `<rect x="${x}" y="${rowY - 12}" width="12" height="12" rx="3" fill="${entry.color}"/>` +
        `<text x="${x + 20}" y="${rowY}" font-size="13" fill="#15201d" font-family="Microsoft YaHei, PingFang SC, sans-serif">${escapeXml(entry.text)}</text>`
      );
    })
    .join("");
}

function buildDownloadSvg(series: ChartSeries, kind: ChartKind): string {
  if (kind === "pie") {
    const values = series.items.map((item) => item.values[0] ?? 0);
    const total = values.reduce((sum, value) => sum + value, 0);
    const radius = 82;
    const center = 105;
    const width = 640;
    // Legend grows with the item count and folds into two columns beyond 6
    // rows — a fixed height used to clip long series (11 groups).
    const columns = series.items.length > 6 ? 2 : 1;
    const legendRows = Math.ceil(series.items.length / columns);
    const height = Math.max(250, 70 + legendRows * 30 + 16);
    let angle = -Math.PI / 2;
    const paths = series.items.map((item, index) => {
      const fraction = (item.values[0] ?? 0) / (total || 1);
      const end = angle + fraction * Math.PI * 2;
      const largeArc = fraction > 0.5 ? 1 : 0;
      const x1 = center + radius * Math.cos(angle);
      const y1 = center + 130 + radius * Math.sin(angle);
      const x2 = center + radius * Math.cos(end);
      const y2 = center + 130 + radius * Math.sin(end);
      const path = `M ${center} ${center + 130} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`;
      angle = end;
      return `<path d="${path}" fill="${PALETTE[index % PALETTE.length]}"/>`;
    });
    const shortLabel = (label: string) => (columns === 2 && label.length > 6 ? `${label.slice(0, 6)}…` : label);
    const entries = series.items.map((item, index) => ({
      color: PALETTE[index % PALETTE.length],
      text: `${shortLabel(item.label)} ${item.values[0]} (${total > 0 ? `${(((item.values[0] ?? 0) / total) * 100).toFixed(1)}%` : "—"})`,
    }));
    const legendX = columns === 2 ? [260, 425] : [280];
    const legendParts: string[] = [];
    for (let columnIndex = 0; columnIndex < columns; columnIndex += 1) {
      const columnEntries = entries.slice(columnIndex * legendRows, (columnIndex + 1) * legendRows);
      legendParts.push(legendBlock(legendX[columnIndex], 66, columnEntries, 30));
    }
    return svg([0, 0, width, height], paths.join("") + legendParts.join(""));
  }

  if (kind === "bars") {
    const single = series.metrics.length === 1;
    const width = 640;
    const rowHeight = 34;
    const height = 64 + series.items.length * rowHeight + (single ? 0 : 30);
    const max = Math.max(...series.items.flatMap((item) => item.values), 1);
    const barX = 200;
    const barMaxWidth = 340;
    const rows = series.items
      .map((item, index) => {
        if (single) {
          const w = Math.max(((item.values[0] ?? 0) / max) * barMaxWidth, 2);
          const y = 60 + index * rowHeight;
          return (
            `<text x="24" y="${y + 14}" font-size="13" fill="#15201d" font-family="Microsoft YaHei, PingFang SC, sans-serif">${escapeXml(item.label.length > 10 ? `${item.label.slice(0, 10)}…` : item.label)}</text>` +
            `<rect x="${barX}" y="${y}" width="${w}" height="20" rx="4" fill="${PALETTE[0]}"/>` +
            `<text x="${barX + w + 10}" y="${y + 15}" font-size="13" fill="#0b4f3d" font-family="Microsoft YaHei, PingFang SC, sans-serif">${item.values[0]}</text>`
          );
        }
        const slot = barMaxWidth / series.metrics.length;
        return (
          `<text x="24" y="${60 + index * rowHeight + 14}" font-size="13" fill="#15201d" font-family="Microsoft YaHei, PingFang SC, sans-serif">${escapeXml(item.label.length > 10 ? `${item.label.slice(0, 10)}…` : item.label)}</text>` +
          item.values
            .map((value, metricIndex) => {
              const w = Math.max((value / max) * (slot * 0.62), 2);
              const x = barX + metricIndex * slot;
              const y = 60 + index * rowHeight;
              return `<rect x="${x}" y="${y}" width="${w}" height="20" rx="3" fill="${PALETTE[metricIndex % PALETTE.length]}"/>`;
            })
            .join("")
        );
      })
      .join("");
    const legend = single
      ? ""
      : legendBlock(24, height - 26, series.metrics.map((name, index) => ({ color: PALETTE[index % PALETTE.length], text: name })), 22);
    return svg([0, 0, width, height], rows + legend);
  }

  // line
  const width = 640;
  const height = 360;
  const padLeft = 48;
  const padBottom = 40;
  const plotWidth = width - padLeft - 20;
  const plotHeight = height - 60 - padBottom - 24;
  const max = Math.max(...series.items.flatMap((item) => item.values), 1);
  function pointFor(index: number, value: number): [number, number] {
    const x = series.items.length === 1 ? padLeft + plotWidth / 2 : padLeft + (index / (series.items.length - 1)) * plotWidth;
    const y = 84 + plotHeight * (1 - value / max);
    return [x, y];
  }
  const grid = [0.25, 0.5, 0.75, 1]
    .map((fraction) => {
      const y = 84 + plotHeight * (1 - fraction);
      return (
        `<line x1="${padLeft}" y1="${y}" x2="${width - 20}" y2="${y}" stroke="#eef1ef" stroke-width="1"/>` +
        `<text x="${padLeft - 8}" y="${y + 4}" text-anchor="end" font-size="11" fill="#8e9995" font-family="Microsoft YaHei, PingFang SC, sans-serif">${Math.round(max * fraction)}</text>`
      );
    })
    .join("");
  const lines = series.metrics
    .map((_name, metricIndex) => {
      const points = series.items
        .map((item, index) => pointFor(index, item.values[metricIndex] ?? 0))
        .map(([x, y]) => `${x},${y}`)
        .join(" ");
      const circles = series.items
        .map((item, index) => {
          const [x, y] = pointFor(index, item.values[metricIndex] ?? 0);
          return `<circle cx="${x}" cy="${y}" r="3.4" fill="#fff" stroke="${PALETTE[metricIndex % PALETTE.length]}" stroke-width="2"/>`;
        })
        .join("");
      return `<polyline points="${points}" fill="none" stroke="${PALETTE[metricIndex % PALETTE.length]}" stroke-width="2.4" stroke-linejoin="round"/>${circles}`;
    })
    .join("");
  const labelStep = Math.ceil(series.items.length / 12);
  const lastIndex = series.items.length - 1;
  const xLabels = series.items
    .map((item, index) => {
      const showLabel =
        series.items.length <= 12 ||
        index === 0 ||
        index === lastIndex ||
        (index % labelStep === 0 && index >= labelStep * 2 && index + labelStep <= lastIndex);
      if (!showLabel) return "";
      const [x] = pointFor(index, 0);
      const anchor = index === 0 ? "start" : index === lastIndex ? "end" : "middle";
      return `<text x="${x}" y="${height - 14}" text-anchor="${anchor}" font-size="11" fill="#4f5a56" font-family="Microsoft YaHei, PingFang SC, sans-serif">${escapeXml(item.label)}</text>`;
    })
    .join("");
  const legend = legendBlock(24, 50, series.metrics.map((name, index) => ({ color: PALETTE[index % PALETTE.length], text: name })), 22);
  return svg([0, 0, width, height], legend + grid + lines + xLabels);
}
