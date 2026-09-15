/** 从粘贴/拖拽的 ``DataTransfer`` 里取出 ``File`` —— 任何类型, 不只是图片。 */

const MIME_EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/webp": "webp",
  "image/bmp": "bmp",
  "application/pdf": "pdf",
};

function pasteFileName(type: string): string {
  const ext =
    MIME_EXT[type] ||
    (type.includes("/") ? type.split("/")[1]!.replace(/[^a-z0-9]/gi, "") : "bin") ||
    "bin";
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  return `paste-${stamp}.${ext}`;
}

/**
 * 剪贴板里的 File 常常没有 name(尤其是截图), 无名文件在附件芯片里会显示成空白。
 * 这里按 MIME 补一个 ``paste-<时间戳>.<ext>``, 于是后续所有路径(芯片、上传、历史恢复)
 * 都只需要认名字, 不必再判一次"有没有名字"。
 */
function normalizeClipboardFile(file: File): File {
  if (file.name && file.name.trim()) return file;
  return new File([file], pasteFileName(file.type || "application/octet-stream"), {
    type: file.type || "application/octet-stream",
    lastModified: file.lastModified,
  });
}

/** 优先 ``files``; 同时扫 ``items`` 里的 file 项(截图只走这条)。 */
export function filesFromClipboard(data: DataTransfer | null | undefined): File[] {
  if (!data) return [];
  const out: File[] = [];
  const seen = new Set<string>();

  const push = (raw: File | null) => {
    if (!raw) return;
    const file = normalizeClipboardFile(raw);
    const key = `${file.name}\0${file.size}\0${file.type}\0${file.lastModified}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(file);
  };

  if (data.files?.length) {
    for (const file of Array.from(data.files)) push(file);
  }
  if (data.items?.length) {
    for (const item of Array.from(data.items)) {
      if (item.kind === "file") push(item.getAsFile());
    }
  }
  return out;
}
