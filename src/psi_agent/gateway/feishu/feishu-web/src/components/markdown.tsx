import { memo, useDeferredValue, useMemo, type MouseEvent as ReactMouseEvent } from "react";
import { preferResultBelowRule } from "../services/assistantDisplay";
import { downloadMatrixTable, matrixToTsv, tableToMatrix } from "../services/mdTable";
import { renderMd } from "../services/renderMd";
import { stripTransferMarkers } from "../services/sendMarkers";

export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

export async function handleTableAction(e: ReactMouseEvent<HTMLElement>) {
  const btn = (e.target as HTMLElement).closest?.("[data-table-action]") as HTMLElement | null;
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const card = btn.closest("[data-md-table]");
  const table = card?.querySelector("table") as HTMLTableElement | null;
  const matrix = tableToMatrix(table);
  if (!matrix.length) return;
  const action = btn.getAttribute("data-table-action");
  if (action === "copy") {
    const tsv = matrixToTsv(matrix);
    await copyText(tsv);
    btn.classList.add("is-done");
    window.setTimeout(() => btn.classList.remove("is-done"), 1400);
    return;
  }
  if (action === "download") {
    btn.classList.add("is-busy");
    try {
      const stamp = new Date().toISOString().slice(0, 10);
      downloadMatrixTable(matrix, `table-${stamp}.tsv`);
    } finally {
      btn.classList.remove("is-busy");
    }
  }
}

export function renderMarkdownHtml(text: string): string {
  const clean = stripTransferMarkers(preferResultBelowRule(text));
  return renderMd(clean);
}

/**
 * 助手气泡 —— **必须 memo, 且解析结果必须 useMemo**。
 *
 * 流式期间每个 delta 都会改 ``messages.at(-1).text``, 于是 ChatThread 整棵树重渲染
 * (它没有窗口化, 也刻意没给 ChatMessageItem 加 memo: 那边的 props 里是内联回调,
 * 每次渲染都是新引用, 加了也恒失效)。这里 memo 的作用是**历史消息直接跳过** ——
 * 它们的 text 没变。没有它时, 每一轮增量都会把会话里**每一条**助手消息重新解析一遍,
 * 开销 O(历史条数 × 平均篇幅); 长会话下直接打满主线程, 表现为操作卡顿(远程调试桥还要
 * 在主线程上做元素拾取, 抢不到时间片)。
 */
export const MarkdownBubble = memo(function MarkdownBubble({ text }: { text: string }) {
  // 正在生长的那条消息 memo 挡不住(text 每个 delta 都变)。整篇解析(marked + 代码高亮 +
  // KaTeX)是同步的纯主线程开销, "每 delta 全量重解析"在长回复下是 O(n^2)。
  // useDeferredValue 让 React 在繁忙时跳过中间值: 解析最多按它跑得完的节奏发生,
  // 而不是每个 delta 一次。渲染结果不变, 只是可能慢半拍。
  const deferredText = useDeferredValue(text);
  const html = useMemo(() => renderMarkdownHtml(deferredText), [deferredText]);
  return (
    <div
      className="focus-chat-bubble"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
});
