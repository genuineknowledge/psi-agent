import { useEffect, useRef, useState, type ReactNode } from "react";
import type { ChatFile, ChatMessage } from "./model";
import { BrandLogo } from "./primitives";
import { readStoredAvatar, readStoredName } from "../services/userProfile";
import { htmlEscape, renderMd } from "../services/renderMd";
import { stripTransferMarkers } from "../services/sendMarkers";
import { downloadMatrixXlsx, matrixToTsv, tableToMatrix } from "../services/mdTable";
import FilePreview from "../components/FilePreview";

function ChatAvatar({ role }: { role: "agent" | "user" }) {
  const [userAvatar, setUserAvatar] = useState(readStoredAvatar);
  const [userName, setUserName] = useState(readStoredName);

  useEffect(() => {
    const sync = () => {
      setUserAvatar(readStoredAvatar());
      setUserName(readStoredName());
    };
    window.addEventListener("storage", sync);
    window.addEventListener("focus", sync);
    return () => {
      window.removeEventListener("storage", sync);
      window.removeEventListener("focus", sync);
    };
  }, []);

  if (role === "agent") {
    return (
      <div className="focus-chat-avatar agent" aria-hidden="true">
        <BrandLogo size="mini" />
      </div>
    );
  }

  const initial = userName.trim().charAt(0).toUpperCase() || "我";
  return (
    <div className="focus-chat-avatar user" aria-hidden="true">
      {userAvatar ? <img src={userAvatar} alt="" /> : <span>{initial}</span>}
    </div>
  );
}

function ChatBlock({
  role,
  children,
}: {
  role: "agent" | "user";
  children: ReactNode;
}) {
  const speaker = role === "agent" ? "HaiTun Agent" : (readStoredName().trim() || "我");
  return (
    <div className={`focus-chat-msg ${role}`}>
      <ChatAvatar role={role} />
      <div className="focus-chat-body">
        <div className="focus-chat-speaker">{speaker}</div>
        {children}
      </div>
    </div>
  );
}

function isPreviewable(name: string) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return ext === "md" || ext === "markdown" || ext === "html" || ext === "htm";
}

async function handleTableAction(e: React.MouseEvent) {
  const btn = (e.target as HTMLElement).closest?.("[data-table-action]") as HTMLElement | null;
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const card = btn.closest("[data-md-table]");
  const table = card?.querySelector("table") as HTMLTableElement | null;
  const matrix = tableToMatrix(table);
  if (!matrix.length) return;
  const action = btn.getAttribute("data-table-action");
  if (action === "copy") {
    const tsv = matrixToTsv(matrix);
    try {
      await navigator.clipboard.writeText(tsv);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = tsv;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    btn.classList.add("is-done");
    window.setTimeout(() => btn.classList.remove("is-done"), 1400);
    return;
  }
  if (action === "download") {
    btn.classList.add("is-busy");
    try {
      const stamp = new Date().toISOString().slice(0, 10);
      await downloadMatrixXlsx(matrix, `table-${stamp}.xlsx`);
    } finally {
      btn.classList.remove("is-busy");
    }
  }
}

/** Split-mode right pane: v1-like chat (MD tables, file preview chips). */
export function FocusChatThread({
  messages,
  typing,
  title,
}: {
  messages: ChatMessage[];
  typing: boolean;
  title: string;
}) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const [preview, setPreview] = useState<ChatFile | null>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, typing]);

  const hasContent = messages.some((m) => m.text.trim() || (m.files?.length ?? 0) > 0) || typing;

  return (
    <div
      className="focus-chat-thread"
      ref={scrollerRef}
      aria-label={`${title} 的对话`}
      onClick={(e) => void handleTableAction(e)}
    >
      {!hasContent && (
        <div className="focus-chat-empty">
          <div className="focus-chat-avatar agent" aria-hidden="true">
            <BrandLogo size="mini" />
          </div>
          <p>向 Agent 发送消息，开始围绕「{title}」继续推进。</p>
        </div>
      )}
      {messages.map((message, index) => {
        const clean = stripTransferMarkers(message.text);
        const emptyStreaming =
          message.role === "agent" &&
          !clean.trim() &&
          !(message.files?.length) &&
          typing &&
          index === messages.length - 1;
        if (emptyStreaming) {
          return (
            <ChatBlock role="agent" key={`typing-${index}`}>
              <div className="focus-chat-bubble thinking">
                <span className="typing" aria-label="正在输入"><i /><i /><i /></span>
              </div>
            </ChatBlock>
          );
        }
        if (!clean.trim() && !(message.files?.length)) return null;

        const html = message.role === "agent"
          ? renderMd(clean)
          : htmlEscape(clean).replace(/\n/g, "<br>");

        return (
          <ChatBlock role={message.role} key={`${message.role}-${index}`}>
            {clean.trim() ? (
              <div
                className="focus-chat-bubble"
                dangerouslySetInnerHTML={{ __html: html }}
              />
            ) : null}
            {(message.files?.length ?? 0) > 0 && (
              <div className="focus-chat-files">
                {message.files!.map((f, fi) => (
                  <button
                    type="button"
                    key={`${f.name}-${fi}`}
                    className="focus-chat-file-chip"
                    disabled={!isPreviewable(f.name) || !f.data}
                    onClick={() => {
                      if (isPreviewable(f.name) && f.data) setPreview(f);
                    }}
                    title={isPreviewable(f.name) ? `预览 ${f.name}` : f.name}
                  >
                    <span>{f.name}</span>
                    {isPreviewable(f.name) ? <em>预览</em> : null}
                  </button>
                ))}
              </div>
            )}
          </ChatBlock>
        );
      })}
      {typing && messages[messages.length - 1]?.role === "user" && (
        <ChatBlock role="agent">
          <div className="focus-chat-bubble thinking">
            <span className="typing" aria-label="正在输入"><i /><i /><i /></span>
          </div>
        </ChatBlock>
      )}
      {preview && <FilePreview file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
