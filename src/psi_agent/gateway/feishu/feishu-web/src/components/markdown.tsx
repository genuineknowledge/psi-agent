import { type MouseEvent as ReactMouseEvent } from "react";
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

export function MarkdownBubble({ text }: { text: string }) {
  return (
    <div
      className="focus-chat-bubble"
      dangerouslySetInnerHTML={{ __html: renderMarkdownHtml(text) }}
    />
  );
}
