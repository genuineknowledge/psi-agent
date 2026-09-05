"""把 SOP 清单渲染成飞书文档的块, 以及把文档块读回成勾选状态 —— 纯逻辑, 不碰飞书。

刻意为之: 详情页用飞书文档的 todo 块(block_type 17), 而不是多维表格的表单视图。
两条路都实测过:
  - 表单视图: 能用 API 建, 但**不支持按人筛选**(加 filter_info 报 field validation
    failed), 而 bitable 的权限最细只到 base 级 —— 同一个 base 里的表, 能看就是全看。
    所以做不到「一人一份、只有自己能看自己那份」。
  - 文档: todo 块可建、done 状态可读回, 且**权限能精确到单个文档 + 单个人**
    (实测 POST /drive/v1/permissions/:doc/members 授 edit 返回 ok=True)。
    一人一份文档、只授权他本人, 隔离天然成立。

条目身份不写进正文, 而是靠 **block_id → item_id 的映射**: 建文档时飞书会在返回体里
给出每个新建块的 block_id(实测确认)。早先在行尾写「〔item_id〕」标记, 新人会看到一串
英文, 观感差; 零宽字符也只能藏分隔符、藏不住 id 本身。映射存进 state 文件, 于是文档
正文一个多余字符都没有。

阅读类条目的两个理解勾选框(已完全理解/未完全理解)要挤在同一行 —— todo 块是块级元素,
彼此之间没有「同行并排」的排布方式, 硬摆会各占一行。飞书文档的表格(block_type 31)
是唯一能把两个块级元素摆进同一行的容器: 建一张 1 行 2 列的空表, 飞书会自动生成 2 个
格子块(block_type 32), 每个格子各塞一个 todo。这样两个勾选框视觉上就在同一行,
互不占行。表格与格子的 block_id 同样不在建表的返回体里给全, 要读回文档才能拿到
(见 _rookie_sop_docapi.provision_doc), 所以阅读类条目比普通条目多两轮网络往返。

数据仍然以明细表为唯一事实来源: 文档只是新人勾选的界面, 勾完由
rookie_sop_sync_doc 把 done 状态同步回表, 所以 HR 日报的数据源完全不用改。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002
from datetime import date
from typing import Any

# 飞书文档块类型(见 _feishu_impl 的块类型表)
BLOCK_TEXT = 2
BLOCK_HEADING2 = 4
BLOCK_TODO = 17
BLOCK_DIVIDER = 22

# 普通条目只有一个 todo, 勾上即完成。
ROLE_DONE = "done"
# 分节小计块(「到岗准备 3/5」那一行)的角色标记, item_id 位置放模块名
ROLE_TALLY = "tally"
# 超链接后面的统一备注 —— 加粗蓝字在飞书里未必被认成「可点」, 明说比指望领会可靠
LINK_NOTE = "（必读材料，请点击超链接打开阅读）"
# 阅读类条目拆成两个理解勾选(见 build_doc_blocks), 各有自己的角色;
# 「已阅读」已去掉 —— 读没读不重要, 重要的是懂没懂。
ROLE_GOT_IT = "ok"
ROLE_UNCLEAR = "unclear"
# 角色选择那一项的两个框。与 config/rookie_sop.yaml 里的 id 一致。
ROLE_ITEM_ID = "role_confirmed"
ROLE_IS_DEV = "isdev"
ROLE_IS_NONDEV = "isnondev"

_MODULE_EMOJI = {
    "到岗准备": "🏢",
    "必读材料": "📚",
    "搞清楚谁是谁": "🤝",
    "每天怎么干活": "📅",
    "制度知晓": "📋",
    "开发环境": "💻",
}


def _text_run(content: str, bold: bool = False, grey: bool = False) -> dict[str, Any]:
    style: dict[str, Any] = {}
    if bold:
        style["bold"] = True
    if grey:
        style["text_color"] = 5  # 飞书字色枚举: 5 = 灰
    return {"text_run": {"content": content, "text_element_style": style}}


def _linked_run(content: str, url: str, bold: bool = False) -> dict[str, Any]:
    """超链接挂在**原文字**上, 不单独占一行罗列 URL —— 排版更干净。"""
    style: dict[str, Any] = {"link": {"url": url}}
    if bold:
        style["bold"] = True
    return {"text_run": {"content": content, "text_element_style": style}}


def _item_id_of(row: dict[str, Any]) -> str:
    key = str(row.get("记录键") or "")
    return key.rsplit(":", 1)[-1] if ":" in key else key


def _todo(elements: list[dict[str, Any]], done: bool) -> dict[str, Any]:
    return {"block_type": BLOCK_TODO, "todo": {"elements": elements, "style": {"done": done}}}


def module_emoji(module: str) -> str:
    return _MODULE_EMOJI.get(module, "▸")


def build_doc_blocks(
    rows: list[dict[str, Any]],
    *,
    name: str,
    today: date | None = None,
    sop_url: str = "",
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """渲染文档根节点的块, 并给出「第 N 个可追踪块对应哪个条目」的顺序表。

    返回 (blocks, slots): slots 与 blocks 里**可追踪块**(todo 或表格)的出现顺序
    一一对应。每项是 (item_id, role):
      - role 非空 —— 这一个块就是 todo, 直接按 role 存进 block_map。
      - role == "" —— 这一个块是表格(阅读类条目的理解勾选放在表格的两个格子里,
        见下), 建表的返回体不会给出格子 block_id, 要等 provision_doc 读回文档、
        发现两个格子后, 再往各自格子里追加一个 todo, 那两个新 todo 才是真正要存
        进 block_map 的对象。table 本身的 block_id 用完即弃。

    普通条目只有一个 todo(role=ROLE_DONE), 勾上即完成。

    阅读类条目(有必读链接)排两个理解勾选, 并排在同一行:
        📖 标题(超链接)
        ☐ 已完全理解        ☐ 未完全理解（会找人问清楚）
    「已阅读」已去掉 —— 读没读不重要, 重要的是懂没懂。todo 块是块级元素, 彼此没有
    「同行并排」的排布方式, 所以用一张 1 行 2 列的表格(block_type 31)当容器,
    两个理解勾选各占一个格子(block_type 32), 视觉上就在同一行。
    两者语义互斥, 但飞书的 todo 块之间**没有互斥机制**(勾一个不会自动取消另一个),
    所以互斥由 read_doc_state 裁决: 两个都勾时以「未完全理解」为准 —— 宁可让 HR
    多看一眼, 也不要把「没懂」误记成「懂了」。
    """
    blocks: list[dict[str, Any]] = [
        {
            "block_type": BLOCK_TEXT,
            "text": {"elements": [_text_run(f"👋 {name}，这是你的入职卡", bold=True)], "style": {}},
        },
        {
            "block_type": BLOCK_TEXT,
            "text": {
                "elements": [_text_run("逐项打勾即可，进度会自动同步给 HR，无需另行提交。", grey=True)],
                "style": {},
            },
        },
    ]
    if sop_url.strip():
        blocks.append(
            {
                "block_type": BLOCK_TEXT,
                "text": {
                    "elements": [
                        _linked_run("📘 完整 SOP 原文", sop_url.strip()),
                        # 与下面的必读材料同一处理: 加粗蓝字未必被认成可点, 明说一句
                        _text_run(LINK_NOTE, grey=True),
                    ],
                    "style": {},
                },
            }
        )

    slots: list[tuple[str, str]] = []
    modules: list[str] = []
    for row in rows:
        module = str(row.get("模块") or "")
        if module and module not in modules:
            modules.append(module)

    for module in modules:
        module_rows = [r for r in rows if str(r.get("模块") or "") == module]
        done_n = sum(1 for r in module_rows if str(r.get("状态") or "") == "已完成")
        blocks.append({"block_type": BLOCK_DIVIDER, "divider": {}})
        blocks.append(
            {
                "block_type": BLOCK_HEADING2,
                "heading2": {
                    "elements": [
                        _text_run(f"{module_emoji(module)} {module}"),
                        _text_run(f"　{done_n}/{len(module_rows)}", grey=True),
                    ],
                    "style": {},
                },
            }
        )
        # 小计块也登记进 slots(role=ROLE_TALLY, item_id 用模块名) —— 同步后要靠
        # 它把「x/y」改成最新值。不登记的话文档里的小计永远停在发出时那一刻,
        # 用户勾完看到条目划掉了、分节标题却还是 0/5(实测反馈过这个)。
        slots.append((module, ROLE_TALLY))
        for row in module_rows:
            item_id = _item_id_of(row)
            title = str(row.get("项") or "").strip()
            acceptance = str(row.get("验收标准") or "").strip()
            status = str(row.get("状态") or "")
            url = str(row.get("必读链接") or "").strip()
            done = status == "已完成"

            if status == "不适用":
                blocks.append(
                    {
                        "block_type": BLOCK_TEXT,
                        "text": {"elements": [_text_run(f"⚪ {title}　不适用", grey=True)], "style": {}},
                    }
                )
                continue

            if item_id == ROLE_ITEM_ID:
                # 角色选择: 两个互斥的勾选框。勾哪个决定那 5 个 dev_only 项是否适用。
                #
                # 刻意放在文档里而不是入口卡上: 入口卡改成「一条消息 + 详情页链接」后
                # 卡上不再有任何回调按钮, 角色若留在卡上就得为它单独破例。放文档里则与
                # 必读材料的两框同一形态, 新人不用学两种交互。
                #
                # 代价是要等下一次同步(入职当天每 10 分钟)才生效, 而不是点完立刻生效 ——
                # 可以接受: 那 5 个开发项的截止是 Day 7, 差十分钟无妨。
                blocks.append(_todo([_text_run("👤 我是研发人员")], False))
                slots.append((item_id, ROLE_IS_DEV))
                blocks.append(_todo([_text_run("👤 我是非研发人员")], False))
                slots.append((item_id, ROLE_IS_NONDEV))
                continue

            if url:
                # 标题本身就是超链接, 不再单独占一行放 URL。
                # 后面补一句括号备注: 加粗蓝字在飞书里未必被认成"可点", 新人容易
                # 直接勾"已完全理解"而没打开过材料 —— 明说一句比指望他领会更可靠。
                blocks.append(
                    {
                        "block_type": BLOCK_TEXT,
                        "text": {
                            "elements": [
                                _text_run("📖 "),
                                _linked_run(title, url, bold=True),
                                _text_run(LINK_NOTE, grey=True),
                            ],
                            "style": {},
                        },
                    }
                )
                # 两个理解勾选竖排。刻意不再用表格容器: 表格能把两个框摆进同一行
                # (实测可行), 但表格线让阅读区显得很脏; 分栏(grid)虽无边框, 观感仍
                # 不如直接竖排。竖排还免掉「建表 → 读回格子 id → 往格里写」这三步
                # 往返, 建文档快得多。
                blocks.append(_todo([_text_run("💡 已完全理解")], done))
                slots.append((item_id, ROLE_GOT_IT))
                blocks.append(_todo([_text_run("❓ 未完全理解（会找人问清楚）")], False))
                slots.append((item_id, ROLE_UNCLEAR))
                continue

            elements = [_text_run(title, bold=True)]
            if acceptance:
                elements.append(_text_run(f"　{acceptance}", grey=True))
            blocks.append(_todo(elements, done))
            slots.append((item_id, ROLE_DONE))
    return blocks, slots


def read_doc_state(blocks: list[dict[str, Any]], block_map: dict[str, str]) -> tuple[dict[str, bool], list[str]]:
    """从文档块读回 ({item_id: 是否完成}, [勾了「未完全理解」的 item_id])。

    ``block_map`` 是 {block_id: "item_id:role"} —— 建文档时存下的映射。靠它而不是
    文字标记认条目, 所以文档正文没有多余字符; 新人自己新增的块不在映射里, 自然被
    忽略(当作他自己的笔记)。

    阅读类条目: 「已完全理解」与「未完全理解」**任选一个**即算完成 —— 后者也是
    有效回答(读过了、如实说没懂), 动作已经走完, 不该继续催。但勾了「未完全理解」
    的条目会进 unclear 列表单独报给 HR, 所以「没懂」不会被悄悄放过。
    普通条目(role=ROLE_DONE)只有一个 todo, 勾上即完成。
    """
    ticked: dict[str, dict[str, bool]] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("block_type") != BLOCK_TODO:
            continue
        mapped = block_map.get(str(block.get("block_id") or ""))
        if not mapped or ":" not in mapped:
            continue
        item_id, role = mapped.rsplit(":", 1)
        # 小计块(role=tally)不是条目 —— 它的 item_id 位置放的是模块名。
        # 不跳过的话模块名会混进 state, 被当成一个「未完成的条目」参与判定。
        if role == ROLE_TALLY:
            continue
        todo = block.get("todo")
        done = bool((todo or {}).get("style", {}).get("done")) if isinstance(todo, dict) else False
        ticked.setdefault(item_id, {})[role] = done

    state: dict[str, bool] = {}
    unclear: list[str] = []
    for item_id, roles in ticked.items():
        if item_id == ROLE_ITEM_ID:
            # 角色项: 两个框互斥, 都勾了以「非研发」为准 —— 宁可让研发项显示为
            # 不适用(可由 HR 或本人改回), 也不要给非研发的人压 5 个他做不了的项。
            picked = bool(roles.get(ROLE_IS_DEV)) or bool(roles.get(ROLE_IS_NONDEV))
            state[item_id] = picked
            continue
        if roles.get(ROLE_UNCLEAR):
            unclear.append(item_id)
        if ROLE_GOT_IT in roles or ROLE_UNCLEAR in roles:
            # 阅读类: 两个选项**任选一个**即算这一条完成 —— 需求如此。
            # 「未完全理解」也是一种有效回答: 新人已经读过、并如实反馈没懂,
            # 这条动作就算走完了; 剩下的是找人问清楚, 由 unclear 单独报给 HR,
            # 不该因此把这一项一直挂在未完成里催他。
            state[item_id] = bool(roles.get(ROLE_GOT_IT)) or bool(roles.get(ROLE_UNCLEAR))
        else:
            state[item_id] = bool(roles.get(ROLE_DONE))
    return state, unclear


def read_role_choice(blocks: list[dict[str, Any]], block_map: dict[str, str]) -> str:
    """新人在文档里勾的角色: "dev" / "nondev" / ""(还没勾)。

    两个框都勾了以「非研发」为准 —— 宁可让 5 个研发项显示为不适用(可改回),
    也不要给非研发的人压一堆他做不了的项。
    """
    is_dev = False
    is_nondev = False
    for block in blocks:
        if not isinstance(block, dict) or block.get("block_type") != BLOCK_TODO:
            continue
        mapped = block_map.get(str(block.get("block_id") or ""))
        if not mapped or ":" not in mapped:
            continue
        item_id, role = mapped.rsplit(":", 1)
        if item_id != ROLE_ITEM_ID:
            continue
        todo = block.get("todo")
        done = bool((todo or {}).get("style", {}).get("done")) if isinstance(todo, dict) else False
        if role == ROLE_IS_DEV and done:
            is_dev = True
        elif role == ROLE_IS_NONDEV and done:
            is_nondev = True
    if is_nondev:
        return "nondev"
    return "dev" if is_dev else ""


def diff_state(doc_state: dict[str, bool], rows: list[dict[str, Any]]) -> list[str]:
    """文档里已完成、而表里还没记完成的 item_id。

    刻意为之: 只认「未完成 → 完成」这一个方向。反向(表里已完成、文档里被取消勾选)
    不做撤销 —— 已完成是既成事实, 让新人取消勾选就能抹掉记录, 会让 HR 日报的数据
    变得不可信; 真要撤销应当由人工改表。
    """
    by_id = {_item_id_of(r): r for r in rows}
    out: list[str] = []
    for item_id, done in doc_state.items():
        if not done:
            continue
        row = by_id.get(item_id)
        if row is None:
            continue
        if str(row.get("状态") or "") == "未完成":
            out.append(item_id)
    return out
