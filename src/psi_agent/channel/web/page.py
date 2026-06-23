from __future__ import annotations

INDEX_HTML = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>dolphin-agent</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #101114;
    --side: #17181d;
    --panel: #1f2128;
    --panel-2: #262932;
    --panel-3: #181a20;
    --file-panel: #2c303a;
    --line: #363a45;
    --line-soft: #2b2f39;
    --text: #eceff3;
    --muted: #a7adb8;
    --soft: #c8ced8;
    --accent: #16a3a3;
    --accent-2: #e16f54;
    --user: #244946;
    --danger: #ff6b6b;
    --shadow: 0 18px 40px rgba(0, 0, 0, .28);
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    font: 14px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: var(--bg);
    color: var(--text);
    overflow: hidden;
  }
  .app { height: 100vh; display: grid; grid-template-columns: 248px minmax(0, 1fr); }
  .app.sidebar-collapsed { grid-template-columns: 64px minmax(0, 1fr); }
  .sidebar {
    background: var(--side);
    border-right: 1px solid var(--line);
    display: flex;
    flex-direction: column;
    min-width: 0;
    position: relative;
  }
  .brand {
    height: 64px;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 18px;
    border-bottom: 1px solid var(--line);
    font-weight: 700;
    letter-spacing: 0;
  }
  .brand-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .side-toggle {
    position: absolute;
    right: -15px;
    top: 78px;
    z-index: 3;
    width: 30px;
    height: 30px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: var(--panel-2);
    color: var(--text);
    cursor: pointer;
    box-shadow: var(--shadow);
    display: grid;
    place-items: center;
    font-size: 16px;
    line-height: 1;
  }
  .app.sidebar-collapsed .brand { padding: 0 16px; justify-content: center; }
  .app.sidebar-collapsed .brand-name,
  .app.sidebar-collapsed .nav,
  .app.sidebar-collapsed .side-foot { display: none; }
  .app.sidebar-collapsed .side-toggle { right: -15px; top: 78px; }
  .mark {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    display: grid;
    place-items: center;
    color: #071111;
    background: var(--accent);
    font-weight: 800;
  }
  .nav { padding: 14px 10px; display: grid; gap: 6px; }
  .nav-item {
    border: 1px solid var(--line);
    background: var(--panel);
    color: var(--soft);
    height: 38px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    padding: 0 12px;
    gap: 10px;
  }
  .dot { width: 8px; height: 8px; border-radius: 999px; background: var(--accent); }
  .side-foot {
    margin-top: auto;
    padding: 14px 16px;
    border-top: 1px solid var(--line);
    color: var(--muted);
    font-size: 12px;
  }
  .main { min-width: 0; display: grid; grid-template-rows: 64px 1fr auto; height: 100vh; position: relative; }
  .topbar {
    border-bottom: 1px solid var(--line);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 22px;
    background: rgba(16, 17, 20, .94);
  }
  .title { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
  .title strong { font-size: 16px; }
  .title span { color: var(--muted); font-size: 12px; }
  .status { color: var(--muted); font-size: 12px; display: flex; gap: 8px; align-items: center; }
  .status i { width: 7px; height: 7px; border-radius: 999px; background: var(--accent); display: inline-block; }
  #log {
    overflow-y: auto;
    padding: 22px;
    display: flex;
    flex-direction: column;
    gap: 14px;
    overscroll-behavior: contain;
  }
  .row { display: flex; }
  .row.user { justify-content: flex-end; }
  .bubble {
    max-width: min(820px, 78vw);
    border: 1px solid var(--line);
    background: var(--panel);
    border-radius: 8px;
    padding: 12px 14px;
    word-break: break-word;
    box-shadow: var(--shadow);
  }
  .message-stack {
    display: grid;
    gap: 10px;
  }
  .row.user .bubble { background: var(--user); border-color: #32615d; white-space: pre-wrap; }
  .row.agent .bubble { background: var(--panel); }
  .assistant-bubble {
    display: grid;
    gap: 10px;
    min-width: min(520px, 78vw);
    white-space: normal;
  }
  .bubble.err { color: var(--danger); border-color: rgba(255, 107, 107, .45); }
  .trace-panel {
    border: 1px solid var(--line-soft);
    background: var(--panel-3);
    border-radius: 8px;
    overflow: hidden;
  }
  .trace-stack {
    display: grid;
    gap: 8px;
  }
  .trace-stack:empty { display: none; }
  .trace-panel[hidden] { display: none; }
  .trace-panel summary {
    min-height: 32px;
    padding: 7px 10px;
    color: var(--soft);
    cursor: pointer;
    list-style: none;
    font-size: 12px;
    font-weight: 650;
    display: flex;
    align-items: center;
    gap: 8px;
    border-bottom: 1px solid var(--line-soft);
  }
  .trace-summary {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .trace-panel summary::-webkit-details-marker { display: none; }
  .trace-panel summary::before {
    content: "";
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--accent-2);
  }
  .tool-panel summary::before { background: var(--accent); }
  .trace-body {
    max-height: 180px;
    overflow: auto;
    padding: 10px;
    color: var(--muted);
    white-space: pre-wrap;
    font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  }
  .tool-body {
    color: #c9d7d7;
  }
  .trace-text {
    white-space: pre-wrap;
  }
  .tool-waiting {
    margin-top: 8px;
  }
  .markdown-body {
    color: var(--text);
    line-height: 1.62;
  }
  .assistant-loader {
    min-height: 22px;
  }
  .assistant-output:empty {
    display: none;
  }
  .typing-dots { display: inline-flex; align-items: center; gap: 5px; min-height: 22px; }
  .typing-dots[hidden] { display: none; }
  .typing-dots span {
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: var(--muted);
    animation: typing-bounce 1s infinite ease-in-out;
  }
  .typing-dots span:nth-child(2) { animation-delay: .14s; }
  .typing-dots span:nth-child(3) { animation-delay: .28s; }
  @keyframes typing-bounce {
    0%, 80%, 100% { opacity: .35; transform: translateY(0); }
    40% { opacity: 1; transform: translateY(-4px); }
  }
  .markdown-body h1,
  .markdown-body h2,
  .markdown-body h3 {
    margin: 12px 0 6px;
    line-height: 1.25;
  }
  .markdown-body h1 { font-size: 20px; }
  .markdown-body h2 { font-size: 17px; }
  .markdown-body h3 { font-size: 15px; }
  .markdown-body p { margin: 0 0 10px; }
  .markdown-body p:last-child { margin-bottom: 0; }
  .markdown-body ul,
  .markdown-body ol { margin: 6px 0 10px 22px; padding: 0; }
  .markdown-body li { margin: 3px 0; }
  .markdown-body blockquote {
    margin: 8px 0;
    padding: 6px 10px;
    color: var(--soft);
    border-left: 3px solid var(--accent);
    background: rgba(22, 163, 163, .08);
  }
  .markdown-body pre {
    margin: 8px 0;
    padding: 10px;
    overflow: auto;
    border-radius: 8px;
    background: #121419;
    border: 1px solid var(--line-soft);
  }
  .markdown-body code {
    font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    background: #121419;
    border: 1px solid var(--line-soft);
    border-radius: 5px;
    padding: 1px 4px;
  }
  .markdown-body pre code {
    border: 0;
    padding: 0;
    background: transparent;
  }
  .markdown-body a { color: #5bcaca; text-decoration: none; }
  .markdown-body a:hover { text-decoration: underline; }
  .jump-bottom {
    position: absolute;
    right: 26px;
    bottom: 98px;
    z-index: 2;
    width: 38px;
    height: 34px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--panel-2);
    color: var(--text);
    box-shadow: var(--shadow);
    cursor: pointer;
    opacity: 0;
    pointer-events: none;
    transition: opacity .16s ease;
    display: grid;
    place-items: center;
    font-size: 18px;
    line-height: 1;
  }
  .jump-bottom.visible {
    opacity: 1;
    pointer-events: auto;
  }
  .composer-wrap {
    border-top: 1px solid var(--line);
    background: var(--bg);
    padding: 14px 22px 18px;
  }
  .attachments {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 10px;
    min-height: 0;
  }
  .message-files {
    display: grid;
    gap: 8px;
  }
  .attachments:empty,
  .message-files:empty { display: none; }
  .file-card {
    min-height: 46px;
    border: 1px solid var(--line);
    background: var(--file-panel);
    color: var(--soft);
    border-radius: 8px;
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    max-width: 440px;
    text-decoration: none;
  }
  .row.user .file-card { background: rgba(255, 255, 255, .08); border-color: rgba(255, 255, 255, .16); }
  .file-icon {
    width: 30px;
    height: 30px;
    border-radius: 7px;
    background: rgba(22, 163, 163, .16);
    color: #7fdada;
    display: grid;
    place-items: center;
    font-weight: 800;
    font-size: 11px;
    flex: 0 0 auto;
    text-transform: uppercase;
  }
  .file-meta { min-width: 0; display: grid; gap: 1px; }
  .file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }
  .file-sub { color: var(--muted); font-size: 12px; }
  .file-remove {
    width: 24px;
    height: 24px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
    margin-left: auto;
  }
  .composer {
    display: grid;
    grid-template-columns: 40px minmax(0, 1fr) 86px;
    gap: 10px;
    align-items: end;
  }
  textarea {
    width: 100%;
    min-height: 46px;
    max-height: 170px;
    resize: vertical;
    background: var(--panel);
    color: var(--text);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 11px 12px;
    font: inherit;
    outline: none;
  }
  textarea:focus { border-color: var(--accent); }
  button.action, label.action {
    height: 46px;
    border: 0;
    border-radius: 8px;
    display: grid;
    place-items: center;
    background: var(--panel-2);
    color: var(--text);
    cursor: pointer;
    font: inherit;
  }
  button.action.primary { background: var(--accent); color: #041313; font-weight: 700; }
  button:disabled, label.disabled { opacity: .55; cursor: default; }
  input[type=file] { display: none; }
  .drop {
    outline: 2px solid var(--accent);
    outline-offset: -8px;
  }
  @media (max-width: 760px) {
    .app,
    .app.sidebar-collapsed { grid-template-columns: 1fr; }
    .sidebar { display: none; }
    .topbar { padding: 0 14px; }
    #log { padding: 16px 12px; }
    .bubble { max-width: 92vw; }
    .assistant-bubble { min-width: min(360px, 92vw); }
    .jump-bottom { right: 16px; bottom: 92px; }
    .composer-wrap { padding: 12px; }
    .composer { grid-template-columns: 40px minmax(0, 1fr) 70px; }
  }
</style>
</head>
<body>
<div class="app sidebar-collapsed" id="app">
  <aside class="sidebar">
    <div class="brand">
      <div class="mark">D</div>
      <div class="brand-name">dolphin-agent</div>
      <button id="toggleSidebar" class="side-toggle" type="button" title="展开侧边栏" aria-label="切换侧边栏">›</button>
    </div>
    <div class="nav">
      <div class="nav-item"><span class="dot"></span><span>对话</span></div>
    </div>
    <div class="side-foot">工作区会话</div>
  </aside>
  <main class="main">
    <header class="topbar">
      <div class="title"><strong>dolphin-agent</strong><span>网页对话</span></div>
      <div class="status"><i></i><span id="state">就绪</span></div>
    </header>
    <section id="log" aria-live="polite"></section>
    <button id="jumpBottom" class="jump-bottom" type="button" aria-label="回到底部">⤓</button>
    <section class="composer-wrap">
      <div id="attachments" class="attachments"></div>
      <div class="composer">
        <label class="action" id="pick" title="上传文件">+</label>
        <input id="file" type="file" multiple />
        <textarea id="input" placeholder="输入消息给 dolphin-agent..." autofocus></textarea>
        <button id="send" class="action primary">发送</button>
      </div>
    </section>
  </main>
</div>
<script>
const log = document.getElementById('log');
const app = document.getElementById('app');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const fileInput = document.getElementById('file');
const pick = document.getElementById('pick');
const attachmentsEl = document.getElementById('attachments');
const state = document.getElementById('state');
const jumpBottom = document.getElementById('jumpBottom');
const toggleSidebar = document.getElementById('toggleSidebar');
let attachments = [];
let autoScroll = true;

function row(kind, text) {
  const wrap = document.createElement('div');
  wrap.className = 'row ' + kind;
  const stack = document.createElement('div');
  stack.className = 'message-stack';
  const el = document.createElement('div');
  el.className = 'bubble';
  el.textContent = text || '';
  stack.appendChild(el);
  wrap.appendChild(stack);
  log.appendChild(wrap);
  keepScroll();
  return el;
}

function createUserMessage(text, files) {
  const wrap = document.createElement('div');
  wrap.className = 'row user';
  const stack = document.createElement('div');
  stack.className = 'message-stack';
  if (text) {
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;
    stack.appendChild(bubble);
  }
  const fileList = renderFileCards(files, { removable: false, downloadable: false });
  stack.appendChild(fileList);
  wrap.appendChild(stack);
  log.appendChild(wrap);
  keepScroll();
}

function createAssistantMessage() {
  const wrap = document.createElement('div');
  wrap.className = 'row agent';

  const stack = document.createElement('div');
  stack.className = 'message-stack';

  const bubble = document.createElement('div');
  bubble.className = 'bubble assistant-bubble';

  const traces = document.createElement('div');
  traces.className = 'trace-stack';

  const content = document.createElement('div');
  content.className = 'markdown-body';
  const loader = createTypingDots('正在等待回复');
  loader.classList.add('assistant-loader');
  const output = document.createElement('div');
  output.className = 'assistant-output';
  content.append(loader, output);

  const files = document.createElement('div');
  files.className = 'message-files';

  bubble.append(traces, content);
  stack.append(bubble, files);
  wrap.appendChild(stack);
  log.appendChild(wrap);
  keepScroll();

  return {
    bubble,
    content,
    traces,
    files,
    loader,
    output,
    rawContent: '',
    currentThinking: null,
    inlineThinking: null,
    inlineThinkingText: '',
    currentTool: null,
    mediaPaths: new Set(),
  };
}

function setAssistantLoading(view, loading) {
  view.loader.hidden = !loading;
}

function createTypingDots(label) {
  const loader = document.createElement('div');
  loader.className = 'typing-dots';
  loader.setAttribute('aria-label', label || '加载中');
  loader.append(document.createElement('span'), document.createElement('span'), document.createElement('span'));
  return loader;
}

function shouldAutoScroll() {
  return log.scrollHeight - log.scrollTop - log.clientHeight < 80;
}

function updateJumpButton() {
  jumpBottom.classList.toggle('visible', !autoScroll);
}

function scrollBottom() {
  log.scrollTop = log.scrollHeight;
  autoScroll = true;
  updateJumpButton();
}

function keepScroll() {
  if (autoScroll || shouldAutoScroll()) scrollBottom();
  else updateJumpButton();
}

function escapeHtml(text) {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function fileExt(nameOrPath) {
  const clean = (nameOrPath || '').split(/[\\\\/]/).pop() || 'file';
  const parts = clean.split('.');
  if (parts.length < 2) return 'FILE';
  return parts.pop().slice(0, 4) || 'FILE';
}

function fileNameFromPath(path) {
  return (path || '').split(/[\\\\/]/).pop() || 'download';
}

function formatBytes(size) {
  if (!Number.isFinite(size) || size <= 0) return 'File';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return (unit === 0 ? value.toFixed(0) : value.toFixed(1)) + ' ' + units[unit];
}

function downloadUrlForPath(path) {
  return '/api/download?path=' + encodeURIComponent(path);
}

function renderFileCards(files, options = {}) {
  const list = document.createElement('div');
  list.className = 'message-files';
  for (const item of files || []) {
    const card = options.downloadable ? document.createElement('a') : document.createElement('div');
    card.className = 'file-card';
    if (options.downloadable) {
      card.href = item.url || downloadUrlForPath(item.path);
      card.download = item.name || fileNameFromPath(item.path);
    }

    const icon = document.createElement('div');
    icon.className = 'file-icon';
    icon.textContent = fileExt(item.name || item.path);

    const meta = document.createElement('div');
    meta.className = 'file-meta';
    const name = document.createElement('div');
    name.className = 'file-name';
    name.textContent = item.name || fileNameFromPath(item.path);
    const sub = document.createElement('div');
    sub.className = 'file-sub';
    sub.textContent = formatBytes(Number(item.size || 0));
    meta.append(name, sub);
    card.append(icon, meta);

    if (options.removable) {
      const remove = document.createElement('button');
      remove.className = 'file-remove';
      remove.type = 'button';
      remove.textContent = 'x';
      remove.addEventListener('click', () => {
        attachments = attachments.filter((x) => x.id !== item.id);
        renderAttachments();
      });
      card.appendChild(remove);
    }

    list.appendChild(card);
  }
  return list;
}

function renderInlineMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
  html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
  html = html.replace(/(^|\\W)\\*([^*\\n]+)\\*/g, '$1<em>$2</em>');
  html = html.replace(/\\[([^\\]]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return html;
}

function renderMarkdown(source) {
  const lines = source.replace(/\\r\\n/g, '\\n').split('\\n');
  const html = [];
  let paragraph = [];
  let listType = null;
  let inCode = false;
  let codeLines = [];

  function flushParagraph() {
    if (paragraph.length === 0) return;
    html.push('<p>' + renderInlineMarkdown(paragraph.join('\\n')).replace(/\\n/g, '<br>') + '</p>');
    paragraph = [];
  }

  function closeList() {
    if (!listType) return;
    html.push('</' + listType + '>');
    listType = null;
  }

  for (const line of lines) {
    const fence = line.match(/^```/);
    if (fence) {
      if (inCode) {
        html.push('<pre><code>' + escapeHtml(codeLines.join('\\n')) + '</code></pre>');
        codeLines = [];
        inCode = false;
      } else {
        flushParagraph();
        closeList();
        inCode = true;
        codeLines = [];
      }
      continue;
    }

    if (inCode) {
      codeLines.push(line);
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      closeList();
      continue;
    }

    const heading = line.match(/^(#{1,3})\\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const level = heading[1].length;
      html.push('<h' + level + '>' + renderInlineMarkdown(heading[2]) + '</h' + level + '>');
      continue;
    }

    const quote = line.match(/^>\\s?(.+)$/);
    if (quote) {
      flushParagraph();
      closeList();
      html.push('<blockquote>' + renderInlineMarkdown(quote[1]) + '</blockquote>');
      continue;
    }

    const item = line.match(/^\\s*((?:[-*])|(?:\\d+\\.))\\s+(.+)$/);
    if (item) {
      flushParagraph();
      const nextType = item[1].endsWith('.') ? 'ol' : 'ul';
      if (listType && listType !== nextType) closeList();
      if (!listType) {
        listType = nextType;
        html.push('<' + listType + '>');
      }
      html.push('<li>' + renderInlineMarkdown(item[2]) + '</li>');
      continue;
    }

    paragraph.push(line);
  }

  if (inCode) html.push('<pre><code>' + escapeHtml(codeLines.join('\\n')) + '</code></pre>');
  flushParagraph();
  closeList();
  return html.join('');
}

function extractThinkBlocks(text) {
  let visible = '';
  let thinking = '';
  let index = 0;
  while (index < text.length) {
    const start = text.indexOf('<think>', index);
    if (start === -1) {
      visible += text.slice(index);
      break;
    }
    visible += text.slice(index, start);
    const bodyStart = start + '<think>'.length;
    const end = text.indexOf('</think>', bodyStart);
    if (end === -1) {
      thinking += text.slice(bodyStart);
      break;
    }
    thinking += text.slice(bodyStart, end);
    index = end + '</think>'.length;
  }
  return { visible, thinking };
}

function extractMediaAttachments(source) {
  const files = [];
  const visible = source.replace(/(^|\\n)MEDIA:\\s*(.+?)(?=\\n|$)/g, (match, prefix, rawPath) => {
    const path = rawPath.trim();
    if (path) files.push({ path, name: fileNameFromPath(path), url: downloadUrlForPath(path) });
    return prefix;
  });
  return { visible, files };
}

function summarizeTrace(kind, text) {
  const compact = (text || '').replace(/\\s+/g, ' ').trim();
  if (!compact) return kind === 'tool' ? '工具调用' : '思考';
  const sentence = compact.match(/^(.{1,96}?[。.!?]|.{1,96})(?:\\s|$)/);
  return (kind === 'tool' ? '工具：' : '思考：') + (sentence ? sentence[1] : compact.slice(0, 96));
}

function createTracePanel(view, kind, summary) {
  const panel = document.createElement('details');
  panel.className = 'trace-panel ' + (kind === 'tool' ? 'tool-panel' : 'thinking-panel');
  panel.open = true;

  const title = document.createElement('summary');
  const titleText = document.createElement('span');
  titleText.className = 'trace-summary';
  titleText.textContent = summary || (kind === 'tool' ? '工具调用' : '思考');
  title.appendChild(titleText);

  const body = document.createElement('div');
  body.className = 'trace-body ' + (kind === 'tool' ? 'tool-body' : 'thinking-body');
  const textEl = document.createElement('div');
  textEl.className = 'trace-text';
  body.appendChild(textEl);
  let waiting = null;
  if (kind === 'tool') {
    waiting = createTypingDots('等待工具返回');
    waiting.classList.add('tool-waiting');
    waiting.hidden = true;
    body.appendChild(waiting);
  }
  panel.append(title, body);
  view.traces.appendChild(panel);
  return { kind, panel, title: titleText, body, textEl, waiting, text: '' };
}

function updateTracePanel(trace) {
  trace.textEl.textContent = trace.text;
  trace.title.textContent = summarizeTrace(trace.kind, trace.text);
}

function setTraceWaiting(trace, waiting) {
  if (!trace || !trace.waiting) return;
  trace.waiting.hidden = !waiting;
}

function appendThinkingTrace(text, view) {
  if (!view.currentThinking) view.currentThinking = createTracePanel(view, 'thinking', '思考');
  view.currentThinking.text += text;
  updateTracePanel(view.currentThinking);
}

function parseToolSummary(text) {
  const call = text.match(/\\[Tool Call:\\s*([^\\]\\(]+)(?:\\((.*?)\\))?\\]/);
  if (call) return '工具：' + call[1];
  const result = text.match(/\\[Tool Result:\\s*(.*?)\\]/);
  if (result) return summarizeTrace('tool', result[1]);
  return summarizeTrace('tool', text);
}

function appendToolTrace(text, view) {
  const parts = text.replaceAll('][', ']\\n[').match(/\\[Tool (?:Call|Result):[\\s\\S]*?\\](?=\\n\\[Tool |$)/g) || [text];
  for (const part of parts) {
    if (part.startsWith('[Tool Call:') || !view.currentTool) {
      view.currentTool = createTracePanel(view, 'tool', parseToolSummary(part));
      setTraceWaiting(view.currentTool, true);
    }
    view.currentTool.text += (view.currentTool.text ? '\\n' : '') + part;
    view.currentTool.title.textContent = parseToolSummary(view.currentTool.text);
    view.currentTool.textEl.textContent = view.currentTool.text;
    if (part.startsWith('[Tool Result:')) setTraceWaiting(view.currentTool, false);
    view.currentThinking = null;
  }
}

function updateInlineThinkingPanel(view) {
  if (!view.inlineThinkingText) return;
  if (!view.inlineThinking) view.inlineThinking = createTracePanel(view, 'thinking', '思考');
  view.inlineThinking.text = view.inlineThinkingText;
  updateTracePanel(view.inlineThinking);
}

function appendReasoning(text, view) {
  if (text.includes('[Tool Call:') || text.includes('[Tool Result:')) {
    appendToolTrace(text, view);
  } else {
    appendThinkingTrace(text, view);
  }
  keepScroll();
}

function appendContent(text, view) {
  view.rawContent += text;
  const extracted = extractThinkBlocks(view.rawContent);
  view.inlineThinkingText = extracted.thinking;
  updateInlineThinkingPanel(view);
  const media = extractMediaAttachments(extracted.visible);
  const hasVisibleContent = Boolean(media.visible.trim() || media.files.length);
  setAssistantLoading(view, !hasVisibleContent);
  view.output.innerHTML = hasVisibleContent ? renderMarkdown(media.visible) : '';
  view.mediaPaths = new Set(media.files.map((file) => file.path));
  const cards = renderFileCards(
    Array.from(view.mediaPaths).map((path) => ({ path, name: fileNameFromPath(path), url: downloadUrlForPath(path) })),
    { downloadable: true },
  );
  view.files.replaceChildren(...Array.from(cards.childNodes));
  keepScroll();
}

function appendError(text, view) {
  setAssistantLoading(view, false);
  view.bubble.classList.add('err');
  view.content.textContent += text;
  keepScroll();
}

function finishAssistantMessage(view) {
  setAssistantLoading(view, false);
  setTraceWaiting(view.currentTool, false);
  keepScroll();
}

function setBusy(busy) {
  input.disabled = busy;
  sendBtn.disabled = busy;
  pick.classList.toggle('disabled', busy);
  state.textContent = busy ? '生成中' : '就绪';
}

function renderAttachments() {
  attachmentsEl.replaceChildren(...Array.from(renderFileCards(attachments, { removable: true }).childNodes));
}

async function uploadFiles(files) {
  for (const file of files) {
    const data = new FormData();
    data.append('file', file);
    state.textContent = '上传中';
    const resp = await fetch('/api/upload', { method: 'POST', body: data });
    if (!resp.ok) throw new Error(await resp.text());
    attachments.push(await resp.json());
  }
  renderAttachments();
  state.textContent = '就绪';
}

async function send() {
  const message = input.value.trim();
  if (!message && attachments.length === 0) return;
  const sentAttachments = attachments;
  createUserMessage(message, sentAttachments);
  scrollBottom();
  input.value = '';
  attachments = [];
  renderAttachments();
  setBusy(true);

  const agentView = createAssistantMessage();

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, attachments: sentAttachments }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\\n\\n');
      buf = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') {
          finishAssistantMessage(agentView);
          continue;
        }
        let evt;
        try { evt = JSON.parse(data); } catch { continue; }
        if (evt.error) {
          appendError(evt.error, agentView);
          continue;
        }
        if (evt.reasoning) appendReasoning(evt.reasoning, agentView);
        if (evt.content) appendContent(evt.content, agentView);
      }
      keepScroll();
    }
  } catch (e) {
    appendError('连接错误：' + e, agentView);
  } finally {
    setBusy(false);
    input.focus();
  }
}

log.addEventListener('scroll', () => {
  autoScroll = shouldAutoScroll();
  updateJumpButton();
});
jumpBottom.addEventListener('click', scrollBottom);
toggleSidebar.addEventListener('click', () => {
  const collapsed = app.classList.toggle('sidebar-collapsed');
  toggleSidebar.textContent = collapsed ? '›' : '‹';
  toggleSidebar.title = collapsed ? '展开侧边栏' : '收起侧边栏';
});
pick.addEventListener('click', () => { if (!input.disabled) fileInput.click(); });
fileInput.addEventListener('change', async () => {
  try { await uploadFiles(fileInput.files); }
  catch (e) { row('agent', '上传失败：' + e).classList.add('err'); }
  finally { fileInput.value = ''; }
});
sendBtn.addEventListener('click', send);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
});
document.body.addEventListener('dragover', (e) => { e.preventDefault(); document.body.classList.add('drop'); });
document.body.addEventListener('dragleave', () => document.body.classList.remove('drop'));
document.body.addEventListener('drop', async (e) => {
  e.preventDefault();
  document.body.classList.remove('drop');
  if (input.disabled) return;
  try { await uploadFiles(e.dataTransfer.files); }
  catch (err) { row('agent', '上传失败：' + err).classList.add('err'); }
});
</script>
</body>
</html>
"""
