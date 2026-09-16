import { useEffect, useState } from "react";
import { FileText, FolderOpen, X } from "lucide-react";
import { revealWorkspacePath } from "../api";
import { isBlobPreviewable } from "../services/filePreview";
import { ArtifactFileBody } from "./artifact-file-body";

/**
 * 一个任务的交付物抽屉: 左侧文件列表, 右侧预览。
 *
 * 数据获取重写过 —— 内容一律由 ArtifactFileBody 走**带鉴权的交付物路由**拉
 * (``/feishu/sessions/{id}/files``), 组件本身只持「当前选中哪个文件」。PR 版在这里自己
 * 缓存 base64 并和 App 的状态互相同步, 两份真相。
 */
export function ArtifactDrawer({
  sessionId,
  taskTitle,
  files,
  filePathOf,
  initialFile,
  pending,
  onSave,
  onClose,
}: {
  /** 会话 id —— 预览用它走带鉴权的交付物路由(见 ArtifactFileBody 的说明)。 */
  sessionId: string;
  taskTitle: string;
  files: string[];
  filePathOf: (name: string) => string | undefined;
  initialFile?: string;
  pending?: boolean;
  onSave?: () => void;
  onClose: () => void;
}) {
  const [active, setActive] = useState(initialFile || files[0] || "");
  const activePath = active ? filePathOf(active) : undefined;

  useEffect(() => {
    if (initialFile) setActive(initialFile);
  }, [initialFile]);

  return (
    <div className="preview-drawer-shell">
      <button type="button" className="preview-scrim" aria-label="关闭交付物" onClick={onClose} />
      <aside className="preview-drawer wide" role="dialog" aria-modal="true" aria-label={`${taskTitle} 的交付物`}>
        <header className="preview-drawer-header">
          <div className="preview-title-wrap">
            <div className="preview-title">交付物</div>
            <em className="preview-task-name">{taskTitle}</em>
          </div>
          <div className="preview-actions">
            {pending && onSave && (
              <button
                type="button"
                className="preview-text-btn"
                onClick={onSave}
              >
                保存到成果库
              </button>
            )}
            {activePath && (
              <button
                type="button"
                className="preview-icon-btn"
                title="在文件夹中显示"
                aria-label="在文件夹中显示"
                onClick={() => void revealWorkspacePath(activePath).catch(() => undefined)}
              >
                <FolderOpen size={16} />
              </button>
            )}
            <button type="button" className="preview-icon-btn" title="关闭" aria-label="关闭" onClick={onClose}>
              <X size={16} />
            </button>
          </div>
        </header>
        <div className="artifact-drawer-split">
          <nav className="artifact-file-list" aria-label="交付物列表">
            {files.length === 0 ? (
              <div className="artifact-file-empty">这个任务还没有交付物</div>
            ) : (
              files.map((f) => (
                <button
                  key={f}
                  type="button"
                  className={`artifact-file-item${f === active ? " is-active" : ""}`}
                  onClick={() => setActive(f)}
                  disabled={!isBlobPreviewable(f)}
                  title={isBlobPreviewable(f) ? f : `${f} (暂不支持预览)`}
                >
                  <FileText size={15} />
                  <span>{f}</span>
                </button>
              ))
            )}
          </nav>
          <div className="artifact-file-pane">
            {active && activePath ? (
              <ArtifactFileBody sessionId={sessionId} path={activePath} name={active} />
            ) : (
              <div className="artifact-file-empty">选择左侧文件查看内容</div>
            )}
          </div>
        </div>
      </aside>
    </div>
  );
}
