import { useEffect, useState } from "react";
import { readWorkspaceFile } from "../api";
import { decodeBase64Text, mimeOf, previewKindOf } from "../services/filePreview";
import { renderMarkdownHtml } from "./markdown";

/**
 * 单个交付物的内容区。数据走 ``GET /workspace/file`` (后端已存在)。
 *
 * 注意后端**总是**返回 ``{name, data, path}`` 且 ``data`` 是 base64 (见
 * ``_workspace_manager.read_file``) —— 没有「原始字节」这种响应形式, 所以图片走 data URL,
 * 文本要先解 base64 再显示。二进制格式本轮不预览, 见 services/filePreview.ts。
 */
export function ArtifactFileBody({ path, name }: { path: string; name: string }) {
  const [data, setData] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const kind = previewKindOf(name);

  useEffect(() => {
    let alive = true;
    if (!path || kind === "none") {
      setData("");
      return;
    }
    setLoading(true);
    setError("");
    readWorkspaceFile(path)
      .then((f) => {
        if (alive) setData(f.data || "");
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [path, kind]);

  if (kind === "none") return <div className="artifact-file-empty">该格式暂不支持预览</div>;
  if (loading) return <div className="artifact-file-empty">加载中…</div>;
  if (error)
    return (
      <div className="artifact-file-empty" role="alert">
        {error}
      </div>
    );
  if (!data) return <div className="artifact-file-empty">文件为空</div>;

  if (kind === "image") {
    return <img className="artifact-file-image" src={`data:${mimeOf(name)};base64,${data}`} alt={name} />;
  }

  const text = decodeBase64Text(data);
  if (kind === "markdown") {
    return <div className="artifact-file-md" dangerouslySetInnerHTML={{ __html: renderMarkdownHtml(text) }} />;
  }
  if (kind === "html") {
    // 交付物 HTML 不进主文档: 隔到 sandbox iframe 里, 免得脚本/样式污染整个应用。
    return <iframe className="artifact-file-frame" title={name} sandbox="" srcDoc={text} />;
  }
  return <pre className="artifact-file-text">{text}</pre>;
}
