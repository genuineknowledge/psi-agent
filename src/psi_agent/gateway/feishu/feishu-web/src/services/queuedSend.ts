/** 一条等当前回合跑完再发出去的跟进消息(Cursor 风格)。 */
export type QueuedSend = {
  text: string;
  files: File[];
  /** 输入框上方那条排队芯片里显示的内容。 */
  display: string;
};

/**
 * 攒一条排队消息。空文本且无附件 → ``null``(不排队)。
 *
 * 纯附件时 ``display`` 要有话说: 芯片里只显示文件名列表, 前缀由调用方给(中文文案属于
 * 调用方的 UI 关注点, 不写进这个纯函数)。
 */
export function buildQueuedSend(
  text: string,
  files: File[],
  uploadedPrefix: string,
): QueuedSend | null {
  const clean = text.trim();
  if (!clean && !files.length) return null;
  const display = clean || `${uploadedPrefix}${files.map((file) => file.name).join("、")}`;
  return { text: clean, files: [...files], display };
}
