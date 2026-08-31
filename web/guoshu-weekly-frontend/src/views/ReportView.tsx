import { useState } from "react";

interface ReportRecord {
  fileName: string;
  createdAt: string;
}

const REPORTS_KEY = "guoshu_weekly_reports";

function readReports(): ReportRecord[] {
  try {
    const raw = localStorage.getItem(REPORTS_KEY);
    const parsed = raw ? (JSON.parse(raw) as ReportRecord[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function rememberReport(fileName: string): ReportRecord[] {
  const next = [{ fileName, createdAt: new Date().toISOString() }, ...readReports()].slice(0, 20);
  localStorage.setItem(REPORTS_KEY, JSON.stringify(next));
  return next;
}

/**
 * Report view (P1-1): generate the weekly summary Word document and download
 * it. The document is built deterministically by the BFF from the取数 service
 * — it is generated material, not a chat transcript (plan 5.4).
 *
 * Plan 6.1 also asks for a download list: every generation is recorded
 * locally (bounded) and can be re-downloaded from the list.
 */
export function ReportView() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reports, setReports] = useState<ReportRecord[]>(readReports);

  /** Fetch the document and trigger the browser download; returns the
   * server-chosen filename so callers can record it. */
  async function downloadReport(): Promise<string> {
    const response = await fetch("/api/reports/weekly-summary");
    if (!response.ok) {
      const detail = (await response.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(detail?.detail ?? `生成失败(${response.status})`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    // Follow the server's dated filename (weekly-summary-2026-08-31.docx)
    // so accumulated reports never collide.
    const disposition = response.headers.get("content-disposition") ?? "";
    const match = /filename="([^"]+)"/.exec(disposition);
    link.download = match ? match[1] : "weekly-summary.docx";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    return link.download;
  }

  async function generate() {
    setBusy(true);
    setError("");
    try {
      const fileName = await downloadReport();
      setReports(rememberReport(fileName));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="workspace">
      <div className="reportCard">
        <div className="conversationHeader">
          <div>
            <div className="eyebrow">材料生成</div>
            <h1>周报总结报告</h1>
          </div>
        </div>
        <p className="reportIntro">
          基于正式周报数据生成一份领导视角的周报总结(Word 文档),包含:
        </p>
        <ul className="reportOutline">
          <li>总体:正式任务总数与业务状态分布</li>
          <li>看板对比:技术组 / 集团组任务规模</li>
          <li>进展时效:各看板最新进展时间</li>
          <li>滞后风险:近 30 天滞后统计</li>
        </ul>
        <button className="generateButton" onClick={() => void generate()} disabled={busy}>
          {busy ? "生成中…" : "生成周报总结"}
        </button>
        {error && <div className="loginError">{error}</div>}
        {reports.length > 0 && (
          <div className="reportHistory">
            <div className="historyRole">已生成的报告</div>
            {reports.map((report) => (
              <div className="reportHistoryRow" key={report.createdAt}>
                <span className="reportFileName">{report.fileName}</span>
                <small>{report.createdAt.slice(0, 16).replace("T", " ")}</small>
                <button className="reportDownloadButton" onClick={() => void generate()} disabled={busy}>
                  下载
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="composerHint">报告由服务端直接取数生成,不依赖对话轮次 · 数据来源:演示库(weekly_mock)</div>
      </div>
    </div>
  );
}
