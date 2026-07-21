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
  Zap,
} from "lucide-react";
import { type FormEvent, useRef, useState } from "react";
import { NEW_TASK_PRESETS } from "./demo-fixtures";
import { OVERVIEW_LABEL, type ChatMessage, type Task, type TaskTemplate } from "./model";
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
  onCreate: (title: string, category: string) => Task | Promise<Task>;
  onViewTask: (task: Task) => void;
}) {
  const [conversation, setConversation] = useState<ChatMessage[]>([]);
  const [typing, setTyping] = useState(false);
  const [createdTask, setCreatedTask] = useState<Task | null>(null);
  const [attachmentName, setAttachmentName] = useState("");
  const attachmentRef = useRef<HTMLInputElement | null>(null);

  const submitConversation = async (event: FormEvent) => {
    event.preventDefault();
    const clean = draft.trim();
    if (!clean || typing) return;

    setConversation((current) => [...current, { role: "user", text: clean }]);
    setDraft("");
    setTyping(true);

    try {
      if (!createdTask) {
        const task = await onCreate(clean, category);
        setCreatedTask(task);
        setConversation((current) => [
          ...current,
          { role: "agent", text: "任务已创建，Agent 正在处理。您可以继续补充，或打开任务卡查看流式回复。" },
        ]);
      } else {
        // Follow-ups happen on the task card (Gateway session); nudge the user there.
        setConversation((current) => [
          ...current,
          { role: "agent", text: "请打开任务卡继续对话——后续消息会写入同一 Gateway Session。" },
        ]);
      }
    } catch (e) {
      const err = e instanceof Error ? e.message : String(e);
      setConversation((current) => [
        ...current,
        { role: "agent", text: `创建失败：${err}` },
      ]);
    } finally {
      setTyping(false);
    }
  };

  return (
    <section className={`new-task-workspace ${createdTask ? "conversation-mode" : ""}`} aria-label="新建任务对话">
      <div className="new-task-ambient one" />
      <div className="new-task-ambient two" />

      <div className="new-task-center">
        <div className="new-task-brand" aria-hidden="true">
          <BrandLogo size="hero" />
          <span>HAITUN AGENT</span>
        </div>

        <div className="new-task-greeting">
          <span className="eyebrow">新建任务</span>
          <h1>{createdTask ? "任务已经开始推进" : "有什么可以帮您？"}</h1>
          <p>{createdTask ? "您可以继续补充要求，或者先去查看任务卡。" : "描述希望得到的结果、截止时间，以及手头已有的材料。"}</p>
        </div>

        {conversation.length > 0 && (
          <div className="new-task-conversation" aria-live="polite">
            {conversation.map((message, index) => (
              <div className={`new-task-message ${message.role}`} key={`${message.role}-${index}`}>
                {message.role === "agent" && <AgentMark />}
                <p>{message.text}</p>
              </div>
            ))}
            {typing && <div className="new-task-message agent"><AgentMark /><span className="typing"><i /><i /><i /></span></div>}
          </div>
        )}

        {!createdTask && (
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
                  <Icon size={16} />
                  <span>{preset.label}</span>
                </button>
              );
            })}
          </div>
        )}

        <form className="new-task-composer" onSubmit={submitConversation}>
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder={createdTask ? "继续补充要求…" : "描述一个任务，Agent 会先与您确认目标…"}
            aria-label="描述新任务"
            autoFocus
          />
          <div className="new-task-composer-actions">
            <button type="button" className="composer-attachment" onClick={() => attachmentRef.current?.click()} aria-label="添加附件"><Paperclip size={18} /></button>
            <input
              ref={attachmentRef}
              type="file"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0] ?? null;
                setAttachmentName(file?.name ?? "");
              }}
            />
            <span><Zap size={13} /> {attachmentName ? `已添加：${attachmentName}` : createdTask ? "打开任务卡可继续对话与传附件" : "发送后创建 Gateway Session 并开始执行"}</span>
            <button type="submit" className="composer-send" disabled={!draft.trim() || typing} aria-label="发送任务描述"><Send size={17} /></button>
          </div>
        </form>

        <div className="new-task-secondary-actions">
          {createdTask ? (
            <button type="button" className="view-created-task" onClick={() => onViewTask(createdTask)}>
              查看任务卡 <ArrowRight size={16} />
            </button>
          ) : (
            <button type="button" onClick={onOpenTemplates}><SquareStack size={15} /> 从任务模板开始</button>
          )}
          <button type="button" onClick={onBack}><ArrowLeft size={15} /> 返回{OVERVIEW_LABEL}</button>
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
