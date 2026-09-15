import { useCallback, useRef, useState, type DragEvent } from "react";
import { filesFromClipboard } from "./clipboardFiles";

/** 这份拖拽载荷里有没有文件(资源管理器 / 别的窗口 / 操作系统)。 */
export function dataTransferHasFiles(data: DataTransfer | null | undefined): boolean {
  if (!data) return false;
  if (data.files && data.files.length > 0) return true;
  const types = data.types ? Array.from(data.types) : [];
  return types.includes("Files");
}

type ComposerFileDropOptions = {
  enabled?: boolean;
  onFiles: (files: File[]) => void;
};

/**
 * 输入区的拖拽落点 —— 与粘贴、回形针按钮走**同一条** ``File[]`` 路径。
 *
 * ``depthRef`` 计进入/离开的层数: 光标从容器移到子元素上时浏览器会先发一次
 * ``dragleave``(父) 再发 ``dragenter``(子), 不记层数就会在子元素边界上疯狂闪烁。
 */
export function useComposerFileDrop({ enabled = true, onFiles }: ComposerFileDropOptions): {
  isFileDragOver: boolean;
  dropProps: {
    onDragEnter: (event: DragEvent<HTMLElement>) => void;
    onDragOver: (event: DragEvent<HTMLElement>) => void;
    onDragLeave: (event: DragEvent<HTMLElement>) => void;
    onDrop: (event: DragEvent<HTMLElement>) => void;
  };
} {
  const [isFileDragOver, setIsFileDragOver] = useState(false);
  const depthRef = useRef(0);

  const reset = useCallback(() => {
    depthRef.current = 0;
    setIsFileDragOver(false);
  }, []);

  const onDragEnter = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!enabled || !dataTransferHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      event.stopPropagation();
      depthRef.current += 1;
      setIsFileDragOver(true);
    },
    [enabled],
  );

  const onDragOver = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!enabled || !dataTransferHasFiles(event.dataTransfer)) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    },
    [enabled],
  );

  const onDragLeave = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!enabled) return;
      event.preventDefault();
      event.stopPropagation();
      depthRef.current = Math.max(0, depthRef.current - 1);
      if (depthRef.current === 0) setIsFileDragOver(false);
    },
    [enabled],
  );

  const onDrop = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!enabled) return;
      event.preventDefault();
      event.stopPropagation();
      reset();
      const files = filesFromClipboard(event.dataTransfer);
      if (files.length) onFiles(files);
    },
    [enabled, onFiles, reset],
  );

  return { isFileDragOver, dropProps: { onDragEnter, onDragOver, onDragLeave, onDrop } };
}
