import {
  ArrowLeft,
  ArrowRight,
  Check,
  FileArchive,
  Paperclip,
  Plus,
  Search,
  Send,
  Sparkles,
  SquareStack,
  X,
} from "lucide-react";
import { type FormEvent, useRef, useState } from "react";
import { NEW_TASK_PRESETS } from "./demo-fixtures";
import { OVERVIEW_LABEL, type Task, type TaskTemplate } from "./model";
import { AgentMark, BrandLogo } from "./primitives";

export function NewTaskWorkspace({
  draft,
  category,
  setDraft,
  setCategory,
  onBack,
  onOpenTemplates,
  onCreate,
  onViewTask,
}: {
  draft: string;
  category: string;
  setDraft: (value: string) => void;
  setCategory: (value: string) => void;
  onBack: () => void;
  onOpenTemplates: () => void;
  /** Same path as overview chat: text + File[] go into the first Session chat turn. */
  onCreate: (title: string, category: string, files?: File[]) => Task | Promise<Task>;
  onViewTask: (task: Task) => void;
}) {
  const [typing, setTyping] = useState(false);
  const [attachments, setAttachments] = useState<File[]>([]);
  const attachmentRef = useRef<HTMLInputElement | null>(null);

  const canSend = Boolean(draft.trim() || attachments.length);

  const submitConversation = async (event: FormEvent) => {
    event.preventDefault();
    const clean = draft.trim();
    if ((!clean && !attachments.length) || typing) return;

    const pendingFiles = attachments;
    setDraft("");
    setAttachments([]);
    setTyping(true);

    try {
      // First message creates the Session and jumps into split focus (left context + right chat).
      const task = await onCreate(clean, category, pendingFiles);
      onViewTask(task);
    } catch {
      setDraft(clean);
      setAttachments(pendingFiles);
      setTyping(false);
    }
  };

  return (
    <section className="new-task-workspace" aria-label="新建任务对话">
      <div className="new-task-ambient one" />
      <div className="new-task-ambient two" />

      <div className="new-task-center">
        <div className="new-task-brand" aria-hidden="true">
          <BrandLogo size="hero" />
          <span>HAITUN AGENT</span>
        </div>

        <div className="new-task-greeting">
          <span className="eyebrow">新建任务</span>
          <h1>有什么可以帮您？</h1>
          <p>描述希望得到的结果、截止时间，以及手头已有的材料。发送后会进入任务分屏继续对话。</p>
        </div>

        <div className="new-task-compose-block">
          {!typing && (
            <div className="new-task-presets">
              {NEW_TASK_PRESETS.map((preset) => {
                const Icon = preset.icon;
                return (
                  <button
                    type="button"
                    key={preset.label}
                    onClick={() => {
                      setDraft(preset.prompt);
                      setCategory(preset.category);
                    }}
                  >
                    <Icon size={14} />
                    <span>{preset.label}</span>
                  </button>
                );
              })}
            </div>
          )}

          {typing && (
            <div className="new-task-compose-status" aria-live="polite">
              <AgentMark /><span className="typing"><i /><i /><i /></span>
            </div>
          )}

          {attachments.length > 0 && (
            <div className="chat-pending-files new-task-pending-files" data-attach-control>
              {attachments.map((file, index) => (
                <span className="chat-pending-chip" key={`${file.name}-${file.size}-${index}`}>
                  <Paperclip size={13} />
                  <em>{file.name}</em>
                  <button
                    type="button"
                    data-attach-control
                    disabled={typing}
                    onClick={() => setAttachments((current) => current.filter((_, i) => i !== index))}
                    aria-label={`移除 ${file.name}`}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}

          <form className="new-task-composer-strip" onSubmit={submitConversation}>
            <button
              type="button"
              className="chat-attach-button"
              data-attach-control
              onClick={() => attachmentRef.current?.click()}
              aria-label="添加附件"
              disabled={typing}
            >
              <Paperclip size={20} />
            </button>
            <input
              ref={attachmentRef}
              type="file"
              multiple
              hidden
              data-attach-control
              onChange={(event) => {
                const next = event.target.files ? Array.from(event.target.files) : [];
                if (next.length) setAttachments((current) => [...current, ...next]);
                event.target.value = "";
              }}
            />
            <input
              type="text"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={typing ? "正在创建任务…" : "描述一个任务，发送后进入分屏与 Agent 对话…"}
              aria-label="描述新任务"
              autoFocus
              disabled={typing}
            />
            <button type="submit" className="send-button" disabled={!canSend || typing} aria-label="发送任务描述">
              <Send size={16} />
            </button>
          </form>
        </div>

        <div className="new-task-secondary-actions">
          <button type="button" onClick={onOpenTemplates} disabled={typing}><SquareStack size={15} /> 从任务模板开始</button>
          <button type="button" onClick={onBack} disabled={typing}><ArrowLeft size={15} /> 返回{OVERVIEW_LABEL}</button>
        </div>
      </div>
    </section>
  );
}
export function TemplateLibrary({
  templates,
  initialSearch = "",
  onBack,
  onUseTemplate,
  onCreateTemplate,
}: {
  templates: TaskTemplate[];
  initialSearch?: string;
  onBack: () => void;
  onUseTemplate: (template: TaskTemplate) => void;
  onCreateTemplate: (title: string, category: string, prompt: string) => void;
}) {
  const [searchText, setSearchText] = useState(initialSearch);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [templateTitle, setTemplateTitle] = useState("");
  const [templateCategory, setTemplateCategory] = useState("自定义模板");
  const [templatePrompt, setTemplatePrompt] = useState("");
  const filteredTemplates = templates.filter((template) => `${template.title}${template.category}${template.description}`.includes(searchText.trim()));

  const saveTemplate = (event: FormEvent) => {
    event.preventDefault();
    if (!templateTitle.trim() || !templatePrompt.trim()) return;
    onCreateTemplate(templateTitle.trim(), templateCategory.trim() || "自定义模板", templatePrompt.trim());
    setTemplateTitle("");
    setTemplatePrompt("");
    setBuilderOpen(false);
  };

  return (
    <section className="template-library" aria-label="任务模板库">
      <header className="template-library-header">
        <div>
          <span className="eyebrow">可复用工作方式</span>
          <h1>任务模板</h1>
          <p>把高频任务沉淀下来，下次只需要补充本次上下文。</p>
        </div>
        <button type="button" className="template-create-button" onClick={() => setBuilderOpen(true)}><Plus size={17} /> 新建模板</button>
      </header>

      <div className="template-toolbar">
        <label><Search size={16} /><input value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="搜索模板或场景" /></label>
        <span>已沉淀 {templates.length} 个模板</span>
      </div>

      <div className="template-grid">
        {filteredTemplates.map((template) => {
          const Icon = template.icon;
          return (
            <article className="template-card" key={template.id}>
              <div className="template-card-top"><span className="template-icon"><Icon size={19} /></span><span className="template-category">{template.category}</span></div>
              <h2>{template.title}</h2>
              <p>{template.description}</p>
              <div className="template-output"><FileArchive size={14} /><span>{template.deliverables.join(" · ")}</span></div>
              <footer><span>{template.cadence}</span><button type="button" onClick={() => onUseTemplate(template)}>使用模板 <ArrowRight size={14} /></button></footer>
            </article>
          );
        })}
      </div>

      <button type="button" className="template-back-link" onClick={onBack}><ArrowLeft size={15} /> 返回{OVERVIEW_LABEL}</button>

      {builderOpen && (
        <div className="template-builder-layer">
          <button type="button" className="template-builder-scrim" onClick={() => setBuilderOpen(false)} aria-label="关闭模板编辑器" />
          <aside className="template-builder" aria-label="新建任务模板">
            <header><div><span className="eyebrow">模板库</span><h2>新建模板</h2></div><button type="button" className="icon-button" onClick={() => setBuilderOpen(false)} aria-label="关闭"><X size={19} /></button></header>
            <form onSubmit={saveTemplate}>
              <label><span>模板名称</span><input value={templateTitle} onChange={(event) => setTemplateTitle(event.target.value)} placeholder="例如：新品发布复盘" autoFocus /></label>
              <label><span>适用场景</span><input value={templateCategory} onChange={(event) => setTemplateCategory(event.target.value)} /></label>
              <label><span>默认任务描述</span><textarea value={templatePrompt} onChange={(event) => setTemplatePrompt(event.target.value)} placeholder="描述目标、输入材料和期望交付物…" /></label>
              <div className="template-builder-note"><Sparkles size={14} /> 保存后会出现在模板库中，使用时仍可修改任务描述。</div>
              <button type="submit" className="primary-button" disabled={!templateTitle.trim() || !templatePrompt.trim()}><Check size={16} /> 保存模板</button>
            </form>
          </aside>
        </div>
      )}
    </section>
  );
}
