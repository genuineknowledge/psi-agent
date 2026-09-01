/**
 * 交付物「能不能在前端直接预览」的判断。
 *
 * 只覆盖**纯文本类**格式 —— 这类文件 ``GET /workspace/file`` 直接返回可渲染的内容,
 * 前端零解析依赖。二进制预览 (pdf / docx / xlsx / pptx) 本轮不做, 所以
 * ``pdfjs-dist`` ``xlsx`` ``docx-preview`` ``pptx-preview`` 都不在 package.json 里。
 * 要加回来的话: 补对应扩展名 + 装依赖 + 在 artifact-file-body 里加分支, 三处一起改。
 */

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif"]);
const MARKDOWN_EXTS = new Set(["md", "markdown"]);
const TEXT_EXTS = new Set([
  "txt",
  "log",
  "json",
  "jsonl",
  "csv",
  "tsv",
  "yaml",
  "yml",
  "toml",
  "ini",
  "xml",
  "py",
  "ts",
  "tsx",
  "js",
  "jsx",
  "css",
  "sh",
  "sql",
]);

export function extensionOf(name: string): string {
  const base = String(name || "").split(/[\\/]/).pop() || "";
  const dot = base.lastIndexOf(".");
  if (dot <= 0 || dot === base.length - 1) return "";
  return base.slice(dot + 1).toLowerCase();
}

export type PreviewKind = "image" | "markdown" | "html" | "text" | "none";

export function previewKindOf(name: string): PreviewKind {
  const ext = extensionOf(name);
  if (!ext) return "none";
  if (IMAGE_EXTS.has(ext) || ext === "svg") return "image";
  if (MARKDOWN_EXTS.has(ext)) return "markdown";
  if (ext === "html" || ext === "htm") return "html";
  if (TEXT_EXTS.has(ext)) return "text";
  return "none";
}

export function isBlobPreviewable(name: string): boolean {
  return previewKindOf(name) !== "none";
}

const IMAGE_MIME: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  bmp: "image/bmp",
  ico: "image/x-icon",
  avif: "image/avif",
  svg: "image/svg+xml",
};

export function mimeOf(name: string): string {
  return IMAGE_MIME[extensionOf(name)] || "application/octet-stream";
}

/**
 * ``/workspace/file`` 的 data 字段是 base64, 文本要解出来才能显示。
 *
 * 不能用 ``atob`` 直接得字符串: 它按 latin-1 解, 中文会变乱码。先还原字节再用 UTF-8 解码。
 */
export function decodeBase64Text(b64: string): string {
  try {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder("utf-8").decode(bytes);
  } catch {
    return "";
  }
}
