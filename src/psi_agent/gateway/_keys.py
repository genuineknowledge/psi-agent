from __future__ import annotations

from typing import Any

from aiohttp import web

# Type-safe AppKeys for gateway configuration/state sharing
AIM_KEY = web.AppKey("aim", Any)
RM_KEY = web.AppKey("rm", Any)
SM_KEY = web.AppKey("sm", Any)
TM_KEY = web.AppKey("tm", Any)
SUM_M_KEY = web.AppKey("sum_m", Any)
SCHEDM_KEY = web.AppKey("schedm", Any)
FM_KEY = web.AppKey("fm", Any)
OAUTH_KEY = web.AppKey("oauth", Any)
WM_KEY = web.AppKey("wm", Any)
CM_KEY = web.AppKey("cm", Any)
HM_KEY = web.AppKey("hm", Any)
TODOM_KEY = web.AppKey("todom", Any)
FAVICON_PATH_KEY = web.AppKey("favicon_path", Any)
APP_NAME_KEY = web.AppKey("app_name", Any)
ATTENTION_KEY = web.AppKey("attention", Any)
DEFAULT_AGENT_KEY = web.AppKey("default_agent", Any)
DEFAULT_WORKSPACE_KEY = web.AppKey("default_workspace", Any)
APPDATA_KEY = web.AppKey("appdata", Any)
AUTHM_KEY = web.AppKey("authm", Any)
