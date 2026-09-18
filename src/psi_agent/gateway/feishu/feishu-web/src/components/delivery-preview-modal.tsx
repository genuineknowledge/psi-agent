import { X } from "lucide-react";
import { ArtifactFileBody } from "./artifact-file-body";

/** 单份交付物的预览弹层。文件内容由 ArtifactFileBody 自己拉, 这里只管框和操作。 */
export function DeliveryPreviewModal({
  sessionId,
  name,
  path,
  onClose,
}: {
  /** 会话 id —— 预览走带鉴权的交付物路由, 归属判定要用它。 */
  sessionId: string;
  name: string;
  path?: string;
  onClose: () => void;
}) {
  return (
    <div className="preview-drawer-shell">
      <button type="button" className="preview-scrim" aria-label="关闭预览" onClick={onClose} />
      <aside className="preview-drawer" role="dialog" aria-modal="true" aria-label={`预览 ${name}`}>
        <header className="preview-drawer-header">
          <div className="preview-title-wrap">
            <div className="preview-title">{name}</div>
          </div>
          <div className="preview-actions">
            <button type="button" className="preview-icon-btn" title="关闭" aria-label="关闭" onClick={onClose}>
              <X size={16} />
            </button>
          </div>
        </header>
        <div className="preview-drawer-body">
          {path ? (
            <ArtifactFileBody sessionId={sessionId} path={path} name={name} />
          ) : (
            <div className="artifact-file-empty">这份交付物还没有本地路径, 无法预览</div>
          )}
        </div>
      </aside>
    </div>
  );
}
