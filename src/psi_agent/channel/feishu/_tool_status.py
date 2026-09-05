"""工具进度状态行: 工具名 → 中文别名, 以及「现在有几个在跑」的状态机。

**为什么是白名单而不是格式化工具名。** 流上的 ``reasoning`` 文本带完整
``json.dumps(args)`` 和结果前 1000 字符, 里面可能是私密文件路径或用户数据 ——
所以卡片上一个字都不能来自那段文本。这里只认工具**名**, 且只认表里有的:
表外的走通用兜底, 而不是回退到工具名本身。工具名同样不该露 (内部工具的存在与
命名是信息), 而且英文名对聊天窗口里的人没有意义。

**为什么状态行是一行而不是步骤清单。** 流里只有「刚调了 X」, 没有「接下来要调
什么」—— 模型不预先声明工具计划, 所以「待办」那一档做不出来。而累加式清单会随
回合越滑越长, 把正文挤出屏幕。

**为什么并发只报个数。** 工具在 agent 侧一个 task group 里并发执行, 于是
``tool_call`` 会连着来好几条才来第一条 ``tool_result``。铺开列名会让这一行的长度
随并发度变化; 报「其中一个 + 还有几个」长度有界, 信息量也够 (用户要的是「还在
动」而不是精确的工具清单)。
"""

from __future__ import annotations

from collections import Counter

STATUS_PREFIX = "⏳ "
"""状态行前缀。

emoji 在飞书 markdown 元素里的**实际**渲染没在真机上验过 (见 spec 的「没验到
什么」)。真机上若显示为豆腐块或占位符, 改这一个常量即可, 不动任何逻辑。
"""

GENERIC_TOOL_LABEL = "正在调用工具"
"""表外工具的兜底文案 —— 刻意不含工具名。"""

TOOL_ALIASES: dict[str, str] = {
    # 本机文件与命令
    "bash": "正在执行命令",
    "read": "正在读取文件",
    "edit": "正在修改文件",
    "write": "正在写入文件",
    "list_dir": "正在浏览目录",
    "find_files": "正在查找文件",
    "search_content": "正在检索代码库",
    # 网络与外部检索
    "fetch": "正在抓取网页",
    "serper_google_search": "正在搜索网络",
    "wiki_search": "正在查维基百科",
    # 会话自身
    "todo": "正在整理待办",
    "clarify": "正在确认需求",
    "tool_search": "正在挑选工具",
    "tool_describe": "正在查看工具说明",
    "tool_search_code": "正在检索工具实现",
    "trigger_manage": "正在设置定时任务",
    # 文档与图像理解
    "describe_image": "正在看图",
    "read_document": "正在读文档",
    "read_pdf": "正在读 PDF",
    "write_word": "正在生成 Word 文档",
    "write_word_from_markdown": "正在生成 Word 文档",
    # 历史与记忆
    "session_keyword_search": "正在检索历史会话",
    "sessions_history": "正在翻阅历史会话",
    "session_status": "正在查看会话状态",
    "memory_search": "正在检索记忆",
    "memory_answer_context": "正在回忆相关内容",
    # 飞书云文档
    "feishu_doc_read": "正在读飞书文档",
    "feishu_doc_create": "正在新建飞书文档",
    "feishu_doc_update_block": "正在修改飞书文档",
    "feishu_doc_append_content": "正在追加飞书文档内容",
    "feishu_doc_list_blocks": "正在梳理文档结构",
    "feishu_docs_search": "正在搜索云文档",
    "feishu_sheet_read": "正在读表格",
    "feishu_sheet_read_grid": "正在读表格",
    "feishu_sheet_find_columns": "正在定位表格列",
    "feishu_sheet_write": "正在写表格",
    "feishu_wiki_list_nodes": "正在浏览知识库",
    "feishu_wiki_list_spaces": "正在浏览知识库",
    # 飞书 IM 与组织
    "feishu_api": "正在调用飞书接口",
    "feishu_message_list": "正在翻阅聊天记录",
    "feishu_message_send": "正在发送消息",
    "feishu_image_get": "正在取图片",
    "feishu_identity_get": "正在查成员信息",
    "feishu_department_members": "正在查部门成员",
    "feishu_permission_list_members": "正在查文档权限",
    "feishu_attendance_query": "正在查考勤",
}
"""工具名 → 中文别名。覆盖 M2 高频集 (``TMPFIX_M2_CORE_TOOLS``), 由判据锁死。

文案与颗粒度属产品侧, 这是可用的一版而非终版; 几个工具共用一句别名是刻意的
(``feishu_sheet_read`` / ``read_grid`` 对用户是同一件事)。
"""


def _label(tool_name: str | None) -> str:
    return TOOL_ALIASES.get(tool_name or "", GENERIC_TOOL_LABEL)


def status_line_for(running: list[str | None]) -> str | None:
    """把「正在跑的工具」渲染成一行状态; 没有在跑的工具时返回 ``None``。

    ``None`` 表示「这一行该消失」, 与空串区分开: 调用方要据此决定是整行抹掉
    还是渲染一个空行。
    """
    if not running:
        return None
    line = f"{STATUS_PREFIX}{_label(running[0])}…"
    others = len(running) - 1
    if others > 0:
        line += f"(另有 {others} 个工具在跑)"
    return line


class ToolStatusTracker:
    """记「现在有哪些工具在跑」, 每次工具边界给出当前该显示的状态行。

    只按工具名计数, 不认 id: 流上没有把 ``tool_call`` 与 ``tool_result`` 配对的
    标识 (两条都只带名字)。所以同名并发两次算两个, 先回来的那条结果只减一 ——
    这正好是用户视角要的「还有一个在跑」。

    计数不会为负: 结果先到或名字对不上时 (``Counter`` 里没有该键) 直接忽略, 否则
    一次错位会让状态行在这一整个回合里永久偏移。
    """

    def __init__(self) -> None:
        self._running: Counter[str] = Counter()
        self._order: list[str] = []

    def on_tool_call(self, tool_name: str | None) -> str | None:
        key = tool_name or ""
        self._running[key] += 1
        self._order.append(key)
        return self._line()

    def on_tool_result(self, tool_name: str | None) -> str | None:
        key = tool_name or ""
        if self._running.get(key):
            self._running[key] -= 1
            if self._running[key] == 0:
                del self._running[key]
            self._order.remove(key)
        return self._line()

    def _line(self) -> str | None:
        # 按到达顺序取, 于是「第一个」是最早开跑的那个 —— 与用户看到的顺序一致。
        return status_line_for([k or None for k in self._order])
