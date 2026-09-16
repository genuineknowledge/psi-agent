/**
 * 触发浏览器保存一个 Blob —— 宝箱与「导出对话历史」共用。
 *
 * ``revokeObjectURL`` **不能紧跟着 click 做**: 有的浏览器还没开始读那个 URL 就被撤销,
 * 用户下到 0 字节文件（而且不报错）。延迟回收, 代价是一个 URL 多活十秒。
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** 把标题之类做进文件名时的安全化: 去掉路径分隔符与 Windows 保留字符。 */
export function safeFileName(name: string, fallback: string): string {
  const clean = name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").trim();
  return clean || fallback;
}
