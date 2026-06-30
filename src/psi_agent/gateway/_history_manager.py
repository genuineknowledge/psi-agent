from __future__ import annotations

import json

import anyio


class HistoryManager:
    async def get(self, workspace: str, session_id: str) -> list[dict[str, str]]:
        jsonl_path = workspace + "/histories/" + session_id + ".jsonl"
        messages: list[dict[str, str]] = []
        try:
            content = await anyio.Path(str(jsonl_path)).read_text(encoding="utf-8")
            for line in content.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                    role = msg.get("role", "")
                    if role not in ("user", "assistant"):
                        continue
                    text = msg.get("content", "")
                    if text:
                        messages.append({"role": role, "text": text})
                except json.JSONDecodeError:
                    continue
        except FileNotFoundError:
            pass
        return messages
