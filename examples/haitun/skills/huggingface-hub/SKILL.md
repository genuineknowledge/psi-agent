---
name: huggingface-hub
description: "用 Hugging Face 官方 `hf` CLI 在 Hugging Face Hub 上搜索/下载/上传模型与数据集(以及 Spaces)。LOAD whenever 用户提到 'hf' / 'huggingface' / 'Hugging Face' / 'huggingface-cli',或要:搜/列模型与数据集(hf models list、hf datasets list --search)、看模型卡/数据集卡与信息、下载权重或数据集到本地(hf download,含 --include/--exclude/--local-dir/--revision)、上传文件或大文件夹到 Hub(hf upload / hf upload-large-folder)、建/删仓库(hf repos)、登录鉴权(hf auth login)、看/清本地缓存(hf cache)。全部通过 bash 工具跑外部 `hf` CLI,不新增 Python 依赖。NOT for 本地推理/训练本身,也 NOT 替代通用 git(大文件走 hf,不走 git add)。"
category: coding
---

# Hugging Face Hub —— 搜索 / 下载 / 上传模型与数据集(via `hf` CLI)

用 Hugging Face 官方命令行工具 **`hf`**(随 `huggingface_hub` Python 包发布)通过 **bash** 工具
和 Hugging Face Hub 交互:搜索、下载、上传**模型 / 数据集 / Spaces**,管理仓库、鉴权和本地缓存。

- **这是外部 CLI**,不是本仓库的 Python 依赖。agent 只是用 `bash` 调 `hf`,**本 skill 不封装
  Python tool、不新增依赖、不改 pyproject / nuitka / pyinstaller**——和 `comfyui`、`himalaya`、
  `codex` 等 CLI-wrapper skill 一样。
- 默认用**中文**回答,除非用户明显用别的语言。

> 语法基于 **huggingface_hub v1.x**(`hf version` 确认)。重点:CLI 已从旧的
> **`huggingface-cli` 改名为 `hf`**,鉴权子命令全部收进 `hf auth`(如 `hf auth whoami`)。
> 跨版本 flag 会漂移,拿不准就 `hf --help` / `hf <命令> --help`,别照记忆硬套。

## 何时使用

- **搜索 / 发现**:找模型或数据集(按关键词、作者、任务/标签筛),看某个 repo 的信息或 README 卡片。
- **下载**:把模型权重、tokenizer、配置或整个数据集拉到本地目录,支持按模式包含/排除、指定分支或
  commit。
- **上传**:把本地文件或文件夹推到 Hub 上的模型 / 数据集 / Space 仓库(单次提交或可续传的大文件夹)。
- **仓库管理**:建仓库、建分支/标签、删仓库或文件、看讨论/PR。
- **鉴权与缓存**:登录、看当前账号、列/清本地下载缓存。

**不用本 skill:**
- 只是**本地跑推理 / 训练**(用你自己的 Python + transformers/torch),那是代码任务,不是 Hub 操作。
- 只是普通 **git 提交代码**——但注意:往 HF 仓库推**大文件**要用 `hf upload` / `hf upload-large-folder`
  (走 Xet/LFS),别用 `git add` 把几 GB 权重塞进普通 git。

## 安全模型(最高优先级)

- **Token 不落痕**:HF token(`HF_TOKEN` 环境变量,或 `hf auth login` 存到本地)是敏感凭据。
  **不打印、不写进命令回显、不提交、不塞进对话或日志**。需要鉴权时优先用环境变量
  `HF_TOKEN`,或让用户自己 `hf auth login`;`hf auth token` 会把明文 token 打到 stdout——**别主动跑它**,
  除非用户明确要,且不要把结果贴回对话。
- **上传即公开风险**:`hf upload` 默认往仓库写内容。建仓库/上传前确认 **`--private`** 还是公开;
  一旦推上去,即使删了也可能已被抓取/缓存。上传前跟用户确认要传哪些文件、传到哪个 repo、可见性。
- **破坏性操作要确认**:`hf repos delete`(**不可逆**)、`hf repos delete-files`、`hf cache rm` /
  `hf cache prune`(删本地缓存)——动手前告诉用户影响并确认,别无人值守地删。
- **磁盘与流量**:大模型/数据集动辄几十上百 GB。下载/缓存前提醒用户占用,必要时用 `--include`
  只拉需要的文件,别默认全量拉。
- **来源可信**:从 Hub 下载的代码类文件(如自定义 `modeling_*.py`、`trust_remote_code` 相关)等于第三方
  代码,别在未告知用户的情况下执行。

## 安装与探测

`hf` 随 `huggingface_hub` 发布。**先探,缺了照实说,别假装成功**:

```bash
hf version 2>&1 || echo "hf CLI 未安装"
```

安装(任选其一):

```bash
# 方式一:独立安装脚本(官方推荐,自带 hf 可执行文件)
curl -LsSf https://hf.co/cli/install.sh | bash

# 方式二:pip / uv 装 Python 包,自带 hf 命令
pip install -U "huggingface_hub[cli]"
# 或:uv pip install -U "huggingface_hub[cli]"

hf --help          # 看当前版本的确切命令/flag(以真机为准)
hf env             # 打印环境信息(版本、缓存路径、平台)
```

缺失时 bash 会报 `hf: command not found` —— 照实转达用户并给安装命令,别编造输出。

## 鉴权(hf auth)

下载**公开**模型/数据集通常**不用登录**;下载**gated/私有**仓库、以及**任何上传**都要鉴权。

```bash
hf auth whoami                     # 看当前登录的是哪个账号(未登录会提示)
hf auth login                      # 交互式:粘贴 https://huggingface.co/settings/tokens 的 token
hf auth login --add-to-git-credential   # 顺带存进 git 凭据助手(推 git 仓库时省事)
hf auth list                       # 列本地存的 token(不显示明文)
hf auth switch --token-name NAME   # 多 token 间切换
hf auth logout                     # 登出
```

**非交互 / CI 场景**用环境变量,别在命令里明文传 token:

```bash
export HF_TOKEN="hf_xxx"           # 由用户/环境提供;不要在对话里回显这个值
hf auth whoami                     # 有 HF_TOKEN 时自动生效
```

> `hf auth token` 会把明文 token 打到 stdout——见安全模型,**默认别跑**。

## 搜索 / 发现(models / datasets)

模型和数据集是**平行的子命令**:`hf models ...` 和 `hf datasets ...`,用法几乎一样。
用 `--format json` 拿结构化输出,配合 `jq` 解析(需要时 `command -v jq`)。

```bash
# 搜模型:关键词 + 作者 + 标签/任务筛选,按下载量/likes/更新时间排序,限量
hf models list --search "llama" --author meta-llama --limit 20
hf models list --filter text-generation --sort downloads --limit 10 --format json
hf models list --filter "library:transformers" --filter "language:zh" --limit 20

# 搜数据集:同样的 --search / --author / --filter / --sort / --limit
hf datasets list --search "squad" --limit 20
hf datasets list --author HuggingFaceH4 --sort likes --limit 10 --format json

# 看某个 repo 的元信息 / README 卡片(确认名字、大小、文件、gated 状态)
hf models info meta-llama/Llama-3.1-8B --format json
hf models card  meta-llama/Llama-3.1-8B --text        # 打印 README 正文
hf datasets info rajpurkar/squad
hf datasets card rajpurkar/squad --metadata           # 只看卡片里的结构化元数据

# 列 repo 里的文件(不下载),确认要 --include 哪些
hf models list --tree --recursive meta-llama/Llama-3.1-8B   # 列该 repo 的文件树
```

排序值(`--sort`)常见:`downloads`、`likes`、`created`、`modified`(以 `--help` 为准)。
`--filter` 可多次给,叠加筛选(任务、library、language、license 等)。

> **别凭记忆报 repo id / 文件名 / 下载量**。这些必须来自你**刚跑的 `hf ... list/info` 真实输出**,
> 逐字复制。查不到就说查不到,别编一个 `org/model-name` 让用户 404。

## 下载(hf download)

`hf download REPO_ID [FILES...]`。默认下到本地缓存(`~/.cache/huggingface/hub`);要落到指定目录用
`--local-dir`。**`--type` 选 `model`(默认)/ `dataset` / `space`。**

```bash
# 下整个模型仓库到缓存,打印本地路径
hf download meta-llama/Llama-3.1-8B

# 只下需要的文件(强烈推荐,省磁盘/流量):按模式包含/排除
hf download meta-llama/Llama-3.1-8B \
  --include "*.safetensors" "config.json" "tokenizer*" \
  --exclude "*.pth" "original/*"

# 下单个文件
hf download meta-llama/Llama-3.1-8B config.json

# 落到指定目录(而不是缓存),便于直接喂给训练/推理脚本
hf download meta-llama/Llama-3.1-8B --local-dir ./models/llama3-8b

# 下数据集:--type dataset
hf download rajpurkar/squad --type dataset --local-dir ./data/squad

# 指定分支 / tag / commit
hf download meta-llama/Llama-3.1-8B --revision refs/pr/1
hf download some-org/model --revision a1b2c3d4

# 先干跑,只看会下哪些文件、不真下
hf download big-org/big-model --include "*.safetensors" --dry-run
```

关键 flag:`--include` / `--exclude`(glob,可多值)、`--local-dir`、`--revision`、`--cache-dir`、
`--force-download`(忽略缓存重下)、`--max-workers`(并发)、`--dry-run`、`--format`。gated/私有仓库
先鉴权(见上)。

## 上传(hf upload / hf upload-large-folder)

- **单次提交、体量适中** → `hf upload`(可传单文件或文件夹)。
- **大文件夹、要可续传/多 worker** → `hf upload-large-folder`(专为大批量、断点续传设计)。

**上传是写操作,先确认目标 repo、可见性(`--private`)、要传哪些文件**(见安全模型)。仓库不存在
时先建(见下)或依赖 `hf upload` 的自动建仓行为——以 `--help` 为准,拿不准就先 `hf repos create`。

```bash
# 传单个文件到模型仓库:hf upload REPO_ID [LOCAL_PATH] [PATH_IN_REPO]
hf upload my-user/my-model ./model.safetensors model.safetensors

# 传整个文件夹,带提交信息;私有仓库加 --private
hf upload my-user/my-model ./out-dir \
  --commit-message "Add fine-tuned weights" --private

# 只传匹配的文件 / 排除
hf upload my-user/my-model ./out-dir --include "*.safetensors" --exclude "checkpoint-*/*"

# 传到数据集仓库:--type dataset
hf upload my-user/my-dataset ./data --type dataset --commit-message "Initial data"

# 用 PR 的方式提交(不直接写主分支)
hf upload my-user/my-model ./out-dir --create-pr

# 大文件夹,可续传、多 worker(推荐用于几十 GB 的权重/数据)
hf upload-large-folder my-user/my-model ./big-checkpoints --type model --num-workers 8
```

`hf upload` 常见 flag:`--type`、`--revision`、`--private`、`--include` / `--exclude`、`--delete`
(删 repo 里匹配的文件)、`--commit-message` / `--commit-description`、`--create-pr`。
`hf upload-large-folder` 专注大批量:`--num-workers`、`--no-report`、`--no-bars`。

## 仓库管理(hf repos)

```bash
hf repos create my-user/my-model                    # 建模型仓库(默认公开)
hf repos create my-user/my-dataset --type dataset --private
hf repos create my-user/my-space --type space --space-sdk gradio
hf repos list --type model --search llama --limit 20
hf repos branch create my-user/my-model dev         # 建分支
hf repos tag create   my-user/my-model v1.0 -m "release"
hf repos delete my-user/my-model                    # 不可逆!先跟用户确认(见安全模型)
```

## 本地缓存(hf cache)

```bash
hf cache list                       # 列已缓存的仓库/修订版及占用
hf cache list --revisions --sort size --limit 20
hf cache prune --dry-run            # 先看会清哪些"游离"修订版
hf cache prune                      # 真清(删已被取代的旧修订)
hf cache rm REPO_ID --dry-run       # 删某个仓库的缓存前先干跑确认
```

删缓存是本地破坏性操作:先 `--dry-run`,再确认。

## 交付产物

下载/生成的文件在 workspace 内的,用 `[SEND:<绝对路径>]` 单独一行交付给用户
(和 `text_to_speech` / `image-generation` 一样)。大模型/数据集通常太大不适合直接发——
**默认只报本地路径和大小**,除非用户明确要发某个具体文件。

## 排错

| 症状 | 原因 / 处理 |
|------|-------------|
| `hf: command not found` | CLI 未装 → `curl -LsSf https://hf.co/cli/install.sh \| bash` 或 `pip install -U "huggingface_hub[cli]"`。 |
| 用户给的是 `huggingface-cli ...` | 旧名已弃用 → 换成 `hf ...`;鉴权在 `hf auth`(如 `hf auth whoami`)。 |
| `401 Unauthorized` / `Repository Not Found`(其实是私有) | 没登录或没权限 → `hf auth login` 或设 `HF_TOKEN`;确认账号对该 gated/私有仓库有访问权。 |
| `403` gated repo | 该模型需在网页上先同意条款/申请访问 → 让用户去 repo 页面点同意后再下。 |
| 下载很慢 / 中断 | 加 `--max-workers`;大文件夹用 `hf upload-large-folder`(上传)/ 重跑 `hf download`(会续传缓存)。 |
| 磁盘爆了 | 用 `--include`/`--exclude` 只拉需要的;`hf cache prune` 清旧修订;`--local-dir` 指到大盘。 |
| 上传大文件报 LFS/Xet 相关错 | 别用 `git add` 传大文件 → 用 `hf upload` / `hf upload-large-folder`。 |
| `--repo-type` 报未知参数 | 新 CLI 用 **`--type`**(model/dataset/space),不是旧的 `--repo-type`;以 `hf <cmd> --help` 为准。 |
| 搜出来是空的 | 放宽 `--search`/`--filter`,或用 `hf models list --limit ...` 不加 filter 先看;别编造结果。 |

## 与相邻 skill 的关系

- **`comfyui`**:出图/视频/音频时会用 HF token 下模型——那是 comfy-cli 自己的下载路径;要**通用地
  在 Hub 上搜/下/传**任意模型或数据集,用本 skill。
- **`codebase-inspection` / 通用 git**:普通代码提交走 git;但 HF 仓库的**大文件**走 `hf upload`。
- **`leaderboard-snapshot-query`**:要查排行榜/基准分,那个 skill 更专;本 skill 只提供
  `hf datasets leaderboard` 作为顺手入口。

本 skill 归 **coding**:围绕 ML 模型/数据集的获取与发布这类工程操作。
