from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from psi_agent.gateway._ai_manager import AIManager
    from psi_agent.gateway._attention import AttentionHub
    from psi_agent.gateway._chat_manager import ChatManager
    from psi_agent.gateway._history_manager import HistoryManager
    from psi_agent.gateway._session_manager import SessionManager
    from psi_agent.gateway._title_manager import TitleManager
    from psi_agent.gateway._workspace_manager import WorkspaceManager
    from psi_agent.session.agent import SessionAgent


# AI App Keys
PROVIDER: web.AppKey[str] = web.AppKey("provider")
MODEL: web.AppKey[str] = web.AppKey("model")
API_KEY: web.AppKey[str] = web.AppKey("api_key")
BASE_URL: web.AppKey[str] = web.AppKey("base_url")

# Session App Keys
AGENT: web.AppKey[SessionAgent] = web.AppKey("agent")

# Gateway App Keys
AIM: web.AppKey[AIManager] = web.AppKey("aim")
SM: web.AppKey[SessionManager] = web.AppKey("sm")
TM: web.AppKey[TitleManager] = web.AppKey("tm")
WM: web.AppKey[WorkspaceManager] = web.AppKey("wm")
CM: web.AppKey[ChatManager] = web.AppKey("cm")
HM: web.AppKey[HistoryManager] = web.AppKey("hm")
FAVICON_PATH: web.AppKey[str | None] = web.AppKey("favicon_path")
APP_NAME: web.AppKey[str] = web.AppKey("app_name")
ATTENTION: web.AppKey[AttentionHub] = web.AppKey("attention")
