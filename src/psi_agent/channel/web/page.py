from __future__ import annotations

INDEX_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>psi-agent</title>
<style>
  :root { color-scheme: light dark; --bg: #0f1115; --panel: #171a21; --line: #262b36;
    --me: #2b6cb0; --txt: #e6e8ec; --dim: #8b93a1; --accent: #5b8def; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
    background: var(--bg); color: var(--txt); height: 100vh; display: flex; flex-direction: column; }
  header { padding: 12px 16px; border-bottom: 1px solid var(--line); font-weight: 600; }
  header small { color: var(--dim); font-weight: 400; margin-left: 8px; }
  #log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .msg { max-width: 760px; width: fit-content; padding: 10px 14px; border-radius: 12px;
    white-space: pre-wrap; word-wrap: break-word; }
  .user { align-self: flex-end; background: var(--me); color: #fff; }
  .agent { align-self: flex-start; background: var(--panel); border: 1px solid var(--line); }
  .reasoning { align-self: flex-start; color: var(--dim); font-size: 13px; font-style: italic;
    white-space: pre-wrap; max-width: 760px; border-left: 2px solid var(--line); padding-left: 10px; }
  .err { color: #ff6b6b; }
  footer { border-top: 1px solid var(--line); padding: 12px 16px; display: flex; gap: 8px; }
  textarea { flex: 1; resize: none; background: var(--panel); color: var(--txt);
    border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; font: inherit; min-height: 44px; }
  button { background: var(--accent); color: #fff; border: 0; border-radius: 10px;
    padding: 0 18px; font: inherit; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
</style>
</head>
<body>
<header>psi-agent <small>Enter for newline &middot; Ctrl/Cmd+Enter to send</small></header>
<div id="log"></div>
<footer>
  <textarea id="input" placeholder="Message psi-agent..." autofocus></textarea>
  <button id="send">Send</button>
</footer>
<script>
const log = document.getElementById('log');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');

function add(cls, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + cls;
  if (cls === 'reasoning') el.className = 'reasoning';
  el.textContent = text || '';
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

async function send() {
  const message = input.value.trim();
  if (!message) return;
  add('user', message);
  input.value = '';
  input.disabled = sendBtn.disabled = true;

  let reasoningEl = null;
  const agentEl = add('agent', '');

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
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
        if (data === '[DONE]') continue;
        let evt;
        try { evt = JSON.parse(data); } catch { continue; }
        if (evt.error) { agentEl.className = 'msg agent err'; agentEl.textContent += evt.error; continue; }
        if (evt.reasoning) {
          if (!reasoningEl) reasoningEl = add('reasoning', '');
          reasoningEl.textContent += evt.reasoning;
        }
        if (evt.content) agentEl.textContent += evt.content;
      }
      log.scrollTop = log.scrollHeight;
    }
  } catch (e) {
    agentEl.className = 'msg agent err';
    agentEl.textContent += 'Connection error: ' + e;
  } finally {
    input.disabled = sendBtn.disabled = false;
    input.focus();
  }
}

sendBtn.addEventListener('click', send);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
});
</script>
</body>
</html>
"""
