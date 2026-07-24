---
name: notion
description: "读/建/改 Notion 页面与数据库、收发 Markdown —— 通过官方 `ntn` CLI(Notion 出的命令行工具)经 bash 直接调 Notion API,把 Notion 当办公知识库集成。LOAD whenever 用户提到 'Notion' / 'ntn' / 'Notion 页面' / 'Notion 数据库(database/data source)' / 'Notion 知识库',或要:读一个 Notion 页面为 Markdown、在某父页面/数据库下新建页面、编辑已有页面内容、查询数据库/数据源里的记录、上传文件到 Notion、发原始 Notion API 请求。NOT for Obsidian 本地 vault(见 obsidian skill)、其它 wiki/知识库工具,也不做需要浏览器交互授权的 `ntn login`(agent 环境用 NOTION_API_TOKEN)。"
category: knowledge-base
---

# Notion —— 终端里的 Notion 知识库(via 官方 `ntn` CLI)

> 语法基于 **Notion 官方 CLI `ntn`**(makenotion,beta)。命令跨版本会漂移,
> CLI 自带文档,拿不准就 `ntn <命令> --help`、`ntn api ls`、`ntn --docs`,别写死猜参数。

## 定义

用 Notion 官方的 [`ntn`](https://developers.notion.com/cli/get-started/overview) CLI 通过 `bash`
工具读写 Notion。它直接打 Notion API,所以能读页面(拿 Markdown)、建/改页面、查数据库(数据源)、
上传文件、发原始 API 请求。把 Notion 当**办公知识库**用——页面正文以 Markdown 收发,天然贴合。

| 你做 | Skill 教你怎么做 |
|------|------------------|
| 读某个页面 | `ntn pages get <page-id>` → 直接返回 Markdown 正文 |
| 在某处建页面 | `ntn pages create --parent page:<id> --content '<markdown>'` |
| 改页面正文 | `ntn pages edit <page-id> --content '<markdown>'`(部分版本是 `pages update`) |
| 查数据库里的记录 | 先 `ntn datasources resolve <database-id>` 拿 data-source-id → `ntn datasources query <ds-id>` |
| API 没封装的操作 | `ntn api <path> ...`(方法自动推断,`ntn api ls` 列端点) |

**没有专用 tool**——全部经 `bash` 工具调用 `ntn`(就像 himalaya 经 bash 调邮件 CLI)。
**纯 CLI 封装,不新增 Python 依赖,不改 pyproject / nuitka / pyinstaller。**

> 默认用**中文**回答,除非用户明显用别的语言。

## 前置条件

- **安装 `ntn`**(用户级操作,agent 只引导,不代跑):
  - 脚本(mac/Linux 推荐):`curl -fsSL https://ntn.dev | bash`
  - npm(mac/Linux/Windows 都行,需 **Node.js 22+ / npm 10+**):`npm install --global ntn`
  - Winget(Windows):`winget install Notion.ntn` —— **Windows 仅支持 x64(x86-64/AMD64)**
  - `ntn --version` 验证。
- **认证:agent 环境一律用 `NOTION_API_TOKEN`,不要跑 `ntn login`。**
  - `ntn login` 会**开浏览器授权**——在无头/agent 环境**会挂住或直接失败**,别用。
  - 正确做法:让用户在 Notion 建一个 integration 拿 **Personal Access Token**(形如 `ntn_xxx`),
    然后 `export NOTION_API_TOKEN=ntn_xxx`,之后所有 `ntn` 命令直接用。
    该变量**优先级高于 keychain** 里存的登录态。
  - 用 `ntn api v1/users/me` 一条命令验证 token 是否有效、连的是哪个 workspace。
  - **无 keychain 的环境**(Docker / CI / 纯 SSH):`ntn login` 会报 keychain 错;
    若确实要落盘 token,设 `NOTION_KEYRING=0` 让它存明文 `auth.json`(**当机密对待,别提交**),
    或在 `config.json` 里写 `"keyring": false`。config 目录由 `NOTION_HOME` 覆盖。
  - **跑命令前先确认 `NOTION_API_TOKEN` 已设**(`printenv NOTION_API_TOKEN` 只看有没有、
    **别回显值**),没有就停下引导用户配 token,别让命令因缺凭据报错才发现。

## 安全规则(最高优先级)

- **写操作(建页/改页/删页/上传)会改动用户真实工作区,先确认再动手。** 新建页面前把
  「建在哪个父页面/数据库下、标题、正文大意」复述给用户;`pages edit`/`update` 是**覆写正文**,
  改前先 `pages get` 读出现状让用户确认,别盲改。
- **`pages trash` 是把页面丢进回收站**——虽可在 Notion 里恢复,仍属破坏性动作,先确认目标 id 与意图。
- **父/目标 id 拿不准就先查、别猜**;拿不定是哪个页面/数据库时把候选列给用户选。
- **Token 与页面内容都是机密。** `NOTION_API_TOKEN`、`auth.json`、读到的页面/数据库内容都含隐私——
  读到什么**不外传**,只按用户当前请求用;**别把 token 值回显进对话**,按变量名引用。
- **只管 Notion。** 本地 Obsidian vault 归 obsidian skill,别用这个 skill 处理。

## 核心命令

CLI 自带文档,**优先自查**而非猜:`ntn api ls`(列可用 API 端点)、`ntn <命令> --help`、
`ntn --docs`(打印完整官方文档)、`ntn --spec`(精简 OpenAPI 片段)。

### 页面:读 / 建 / 改(正文走 Markdown)

```bash
ntn pages get <page-id>                 # 读页面,直接返回 Markdown 正文
ntn pages get <page-id> --json          # 要结构化就加 --json(版本支持时)

# 建页面:--parent 接 page:<id> / database:<id> / data-source:<id>
ntn pages create --parent page:<parent-id> --content '## 标题

正文,支持 **粗体**、*斜体*、`code`、[链接](https://…)。'

# 正文也可从 stdin 喂(长文/多行更稳):
cat body.md | ntn pages create --parent database:<db-id>

# 改页面正文(覆写;部分版本子命令是 pages update,--help 确认)
ntn pages edit <page-id> --content '<新的 markdown 正文>'

ntn pages trash <page-id>               # 丢进回收站(破坏性,先确认)
```

> **父 ref 的坑**:`pages create` 用**带前缀**的形式 `page:<id>` / `database:<id>` /
> `data-source:<id>`;而裸 `ntn api` 里父级是 `parent[page_id]=<id>` 或 JSON
> `{"parent":{"page_id":"<id>"}}` —— 两处形状不同,别混。
> **Markdown 的坑**:`--content` 的 markdown 支持行内格式(粗体/斜体/代码/链接);
> mention、自定义 emoji、颜色这类要落到 `rich_text`,普通正文用 markdown 就够。

### 数据库 / 数据源:查记录

Notion 现在把可查询的表叫 **data source**,不是直接查 database,需先解析:

```bash
ntn datasources resolve <database-id>          # database-id → 对应 data-source-id(可能多个)
ntn datasources query <data-source-id>         # 查这个数据源里的页面(记录)
ntn datasources query <ds-id> --limit 50 --sort '<…>' --filter '<json>'
ntn datasources query <ds-id> --filter-file filter.json --start-cursor <cursor>
```

`--filter` / `--sort` 的 JSON 结构同 Notion API 的 query 语义,复杂过滤写进文件用 `--filter-file`
更省转义。分页靠返回的 `next_cursor` 配 `--start-cursor` 翻页。

### 文件上传

```bash
ntn files create < image.png                    # 上传本地文件(stdin)
ntn files create --external-url https://example.com/a.png   # 用外链
ntn files list                                  # 列已上传
ntn files get <upload-id>                        # 看某次上传详情
```

### 原始 API 请求(CLI 没封装的都走这里)

```bash
ntn api v1/users/me                              # GET(方法默认 GET)
ntn api v1/users page_size==100                  # GET 带 query 参数(==)
ntn api v1/pages parent[page_id]=<id>            # 有 body 自动推断为 POST
ntn api v1/pages -d '{"parent":{"page_id":"<id>"},"properties":{…}}'  # POST JSON body
ntn api v1/blocks/<id>/children -X PATCH -d '{…}'  # 显式 -X 指定方法
```

方法自动推断(有 body 即 POST),`-X METHOD` 显式覆盖。块级(blocks)操作没有独立子命令,走 `ntn api`。

## 典型工作流:把一段内容沉淀成 Notion 页面

用户说「把这次调研结论建成一个 Notion 页面,放在『研究』页面下」:

1. **确认 token**:`printenv NOTION_API_TOKEN`(只看有没有,别回显值);没有就停下引导配。
2. **定位父页面 id**:用户给链接就从 URL 尾部取 id;拿不准就
   `ntn api v1/search -d '{"query":"研究"}'` 搜,把候选列给用户选,别猜。
3. **复述确认**:把「建在『研究』(id=…)下、标题、正文大意」讲给用户,得到同意。
4. **建页面**(长正文走 stdin 更稳):
   ```bash
   cat conclusion.md | ntn pages create --parent page:<研究页id>
   ```
5. **复核**:`ntn pages get <新页id>` 读回来确认内容与层级对,把新页 id/链接给用户。

## 常见坑

| 症状 | 原因 / 处理 |
|------|-------------|
| `ntn: command not found` | 未安装 → 引导用户 `curl -fsSL https://ntn.dev \| bash` 或 `npm i -g ntn` 或 winget |
| 命令挂住 / 弹浏览器 | 跑了 `ntn login` 走浏览器授权 → agent 环境改用 `NOTION_API_TOKEN`,别 login |
| `unauthorized` / 401 | token 没设、失效,或该 integration 没被 share 到目标页面/数据库 → 让用户在 Notion 页面右上「Connections」里把 integration 加进去 |
| keychain 相关报错 | 无 keychain 环境(Docker/CI/SSH) → 设 `NOTION_KEYRING=0` 或 config.json `"keyring": false` |
| 查 database 报错/查不到 | Notion 现用 data source → 先 `datasources resolve <db-id>` 拿 ds-id 再 `datasources query` |
| `--json` 无效 / 参数对不上 | CLI 版本漂移 → `ntn <命令> --help`、`ntn api ls` 自查,别写死 |
| 父页面报错 | `pages create` 的 parent 要**带前缀** `page:`/`database:`/`data-source:`,和裸 api 的形状不同 |
| markdown 里 mention/颜色没生效 | markdown 只保行内格式;mention/自定义 emoji/颜色需 `rich_text`,用 `ntn api` 直接发 |
| token 泄漏 | 别把 `NOTION_API_TOKEN` 值、`auth.json` 内容回显进对话,按名引用 |
