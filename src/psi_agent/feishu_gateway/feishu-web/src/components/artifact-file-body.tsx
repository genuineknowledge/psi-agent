import { useEffect, useRef } from "react";
import { renderBlobPreview, type BlobPreviewFile } from "../services/renderBlobPreview";

export function ArtifactFileBody({ file }: { file: BlobPreviewFile }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const previewRef = useRef<{ cleanup: () => void } | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let cancelled = false;
    void renderBlobPreview(host, file).then((handle) => {
      if (cancelled) {
        handle.cleanup();
        return;
      }
      previewRef.current = handle;
      if (handle.error) {
        host.replaceChildren();
        const p = document.createElement("p");
        p.className = "preview-unavailable";
        p.textContent = handle.error;
        host.append(p);
      }
      if (handle.notice) {
        const p = document.createElement("p");
        p.className = "preview-partial-notice";
        p.textContent = handle.notice;
        host.prepend(p);
      }
    });
    return () => {
      cancelled = true;
      previewRef.current?.cleanup();
      previewRef.current = null;
    };
  }, [file.name, file.data]);

  return <div className="artifact-file-body" ref={hostRef} />;
}
