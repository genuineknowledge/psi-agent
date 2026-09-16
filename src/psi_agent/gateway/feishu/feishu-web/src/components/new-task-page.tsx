import { useRef } from "react";
import {
  ArrowLeft,
  BookOpen,
  ClipboardList,
  FileText,
  MessageCircle,
  Paperclip,
  Search,
  Send,
  X,
} from "lucide-react";

const PRESETS = [
  {
    label: "学习新知识",
    icon: BookOpen,
    prompt: "请帮我学习以下新知识，提炼核心概念、关键要点和可验证的结论：",
  },
  {
    label: "管理工作SOP",
    icon: ClipboardList,
    prompt: "请帮我梳理并管理以下工作的 SOP，形成清晰可执行的流程步骤：",
  },
  {
    label: "做一份汇报",
    icon: FileText,
    prompt: "请帮我整理一份面向管理层的汇报，材料包括：",
  },
  {
    label: "研究市场或竞品",
    icon: Search,
    prompt: "请研究以下市场或竞品，核验公开来源并给出证据：",
  },
  {
    label: "整理会议行动项",
    icon: MessageCircle,
    prompt: "请把以下会议材料整理为结论和行动项：",
  },
] as const;

export function NewTaskPage({
  draft,
  sending,
  pendingFiles,
  error,
  onDraft,
  onBack,
  onSubmit,
  onAddFiles,
  onRemoveFile,
}: {
  draft: string;
  sending: boolean;
  pendingFiles: File[];
  /**
   * 建会话失败的原因(``sessions.error``)。
   *
   * 非有不可: 建会话失败时 ``createFromDraft`` 直接 return, 页面**什么都不显示** ——
   * 用户看到的是「点了发送没反应」, 然后多半会退回去在别的会话里接着打字(实测那次就是
   * 这么落到一条只读的组织共享会话里的)。失败必须说出来。
   */
  error?: string;
  onDraft: (value: string) => void;
  onBack: () => void;
  onSubmit: () => void;
  onAddFiles: (files: File[]) => void;
  onRemoveFile: (index: number) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const canSubmit = !sending && (draft.trim().length > 0 || pendingFiles.length > 0);

  return (
    <div className="cend2-shell cend2-full">
      <main className="cend2-main">
        <header className="cend2-topbar">
          <div className="cend2-top-left">
            <button type="button" className="cend2-iconbtn" aria-label="返回任务" onClick={onBack}>
              <ArrowLeft size={18} />
            </button>
          </div>
        </header>
        <div className="cend2-newtask-page">
          <div className="cend2-newtask-hero">
            <span className="cend2-newtask-eyebrow">新建任务/聊天</span>
            <h2>有什么可以帮您？</h2>
            <p>描述希望得到的结果、截止时间，以及手头已有的材料。发送后会进入任务分屏继续对话。</p>
          </div>

          {error && <div className="ht-error" role="alert">{error}</div>}

          <div className="new-task-compose-block">
            <div className="new-task-presets">
              {PRESETS.map((preset) => {
                const Icon = preset.icon;
                return (
                  <button
                    key={preset.label}
                    type="button"
                    onClick={() => onDraft(preset.prompt)}
                  >
                    <Icon size={14} />
                    <span>{preset.label}</span>
                  </button>
                );
              })}
            </div>

            {pendingFiles.length > 0 && (
              <div className="chat-pending-files">
                {pendingFiles.map((file, index) => (
                  <span className="chat-pending-chip" key={`${file.name}-${index}`}>
                    <Paperclip size={13} />
                    <em>{file.name}</em>
                    <button
                      type="button"
                      aria-label={`移除 ${file.name}`}
                      onClick={() => onRemoveFile(index)}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            )}

            <form
              className="new-task-composer-strip"
              onSubmit={(event) => {
                event.preventDefault();
                if (canSubmit) onSubmit();
              }}
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                onChange={(event) => {
                  onAddFiles(Array.from(event.target.files || []));
                  event.target.value = "";
                }}
              />
              <button
                type="button"
                className="chat-attach-button"
                aria-label="添加附件"
                disabled={sending}
                onClick={() => fileInputRef.current?.click()}
              >
                <Paperclip size={20} />
              </button>
              <textarea
                autoFocus
                placeholder={sending ? "正在创建任务…" : "描述一个任务，发送后进入分屏与 Agent 对话…"}
                value={draft}
                disabled={sending}
                onChange={(event) => onDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !sending) {
                    event.preventDefault();
                    if (canSubmit) onSubmit();
                  }
                }}
                aria-label="描述新任务"
              />
              <button
                type="submit"
                className="send-button"
                disabled={!canSubmit}
                aria-label="发送任务描述"
              >
                <Send size={16} />
              </button>
            </form>
          </div>

          <div className="cend2-new-actions">
            <button type="button" onClick={onBack}>
              <ArrowLeft size={15} />
              返回任务
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
