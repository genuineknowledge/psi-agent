"""Cross-layer shared wire-format constants for Feishu card callbacks.

The ``<feishu_card_action>`` tag, the batch envelope tag, and the ``NO_REPLY``
silent-reply token are produced by the **channel** and consumed by the
**session** (direct dispatch). Keeping a private copy on each side drifted
silently once already — a renamed tag or token on one side makes the direct
dispatch fall back to the AI turn with no error, or sends a bare "NO_REPLY"
into a chat. One authoritative definition here, imported by both sides.
Precedent: the ``[SEND:]`` marker regex was collapsed into a single top-level
module after two slightly different copies caused exactly this kind of bug.
"""

from __future__ import annotations

import re

CARD_ACTION_TAG = "feishu_card_action"
CARD_ACTION_BATCH_TAG = "feishu_card_action_batch"

CARD_ACTION_PATTERN = re.compile(r"<feishu_card_action>\s*(.*?)\s*</feishu_card_action>", re.DOTALL)
CARD_ACTION_BATCH_PATTERN = re.compile(r"</?feishu_card_action_batch[^>]*>")

# Feishu channel 在 suppress_silent_reply 模式下把该 token 当「无需回复」吞掉
# (见 channel/feishu/client.py 的 _stream_reply)。卡片直调成功时用它保持静默。
SILENT_REPLY = "NO_REPLY"
