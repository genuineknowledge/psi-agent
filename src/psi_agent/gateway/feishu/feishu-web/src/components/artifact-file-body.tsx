import { useEffect, useRef, useState } from "react";
import { readDeliverable } from "../api";
import { decodeBase64Text, mimeOf, previewKindOf } from "../services/filePreview";
import { renderBlobPreview } from "../services/blobPreview";
import { renderMarkdownHtml } from "./markdown";

function BlobPreviewHost({ name, data }: { name: string; data: string }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [notice, setNotice] = useState("");
  const [renderError, setRenderError] = useState("");

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !data) return;
    let alive = true;
    let cleanup = () => {};
    setRenderError("");
    setNotice("");
    void renderBlobPreview(host, { name, data })
      .then((handle) => {
        if (!alive) {
          handle.cleanup();
          return;
        }
        cleanup = handle.cleanup;
        if (handle.error) setRenderError(handle.error);
        if (handle.notice) setNotice(handle.notice);
      })
      .catch((err: unknown) => {
        if (alive) setRenderError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
      cleanup();
    };
  }, [name, data]);

  return (
    <>
      {notice ? <div className="preview-partial-notice">{notice}</div> : null}
      {renderError ? <div className="artifact-file-empty" role="alert">{renderError}</div> : null}
      <div ref={hostRef} className="blob-preview-host" />
    </>
  );
}

/**
 * 单个交付物的内容区。数据走 ``GET /feishu/sessions/{id}/files?path=``(带鉴权的对等路由)。
 *
 * 后端**总是**返回文件原始字节(Content-Disposition 附件), 前端转成 base64 —— 与
 * ``/workspace/file`` 的 ``data`` 字段同形, 所以图片走 data URL、文本先解 base64、
 * 二进制由 ``blobPreview.ts`` 动态渲染, 三种分支都不用改。
 *
 * 为什么不用 ``/workspace/file``: 那条归 desktop 面且不在云上反代白名单里, 预览点开必 404
 * (实测反馈)。对等路由要 session id 才能判定归属, 所以它是一路传下来的必填项。
 */
export function ArtifactFileBody({ sessionId, path, name }: { sessionId: string; path: string; name: string }) {
  const [data, setData] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const kind = previewKindOf(name);

  useEffect(() => {
    let alive = true;
    if (!sessionId || !path || kind === "none") {
      setData("");
      return;
    }
    setLoading(true);
    setError("");
    readDeliverable(sessionId, path)
      .then((base64) => {
        if (alive) setData(base64 || "");
      })
      .catch((err: unknown) => {
        if (!alive) return;
        const message = err instanceof Error ? err.message : String(err);
        // 「不是本会话声明过的交付物」= 文件刚交付、历史行还没写进去。说清楚, 别让用户
        // 以为文件坏了(判据在后端, 见 _session_deliverable_paths)。
        setError(
          message.includes("not a deliverable")
            ? "这个文件刚交付, 还没写进会话记录, 稍后重试或直接下载。"
            : message,
        );
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [sessionId, path, kind]);

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
  if (kind === "blob") {
    return <BlobPreviewHost name={name} data={data} />;
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
