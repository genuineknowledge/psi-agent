# 飞书云文档小组件 —— 在文档里对话 Haitun Agent

在飞书文档正文里插入一个对话小组件: 提问、流式看到回答、按需把回答作为原生块插入文档。

小组件本体由 opdev 上传到飞书 CDN, **不经 Gateway 提供**, 所以它放在 `examples/` 而不是
`src/psi_agent/gateway/spa*` 下 —— 后者会被打进 wheel。

## 为什么后端地址是可配置的

小组件跑在飞书托管的 iframe 里, 其请求受**服务器域名白名单 (CSP)** 限制: 不在白名单里的
域名一律被拦。飞书官方文档没有说明 `localhost` / 明文 `http` 能不能加进白名单 —— 我查了
安全配置页与调试页都未提及。所以这里不赌任何一种行为, 把网关地址做成运行时可配置:
本地调试指向 `http://localhost:8000`, 上线改成你实际的 https 入口, 同一个包都能用。

配置存在**区块的 Record** 里, 会随文档一起走: 同事打开这篇文档时无需再填。代价是
**令牌对该文档的所有可读者可见** —— 它是团队共享密钥, 不要拿它当个人凭据。

## 身份与鉴权

`Service.User.getUserId()` 拿到的 user_id 是**客户端自报值**, 服务端无从验证。因此:

- user_id **只用于会话隔离** (谁的上下文), 不是身份认证;
- 真正的访问控制是 `X-Psi-Addon-Token`, 与网关 `--docs-addon-token` 常量时间比对;
- 网关没配 token 时 `/docs-addon/*` 整体返回 404 —— 不会存在一个无鉴权的对话端点。

会话按 `(doc_token, user_id)` 隔离: 同一个人在不同文档各有独立上下文, 同一篇文档里不同人
也互不可见。这与飞书群聊「整群共用一个 Session」刻意相反 —— 文档里各人是各自在用工具。

## 一、起网关

```bash
cd examples/haitun-workspace
python -m psi_agent gateway \
  --listen http://0.0.0.0:8000 \
  --docs-addon-token "$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  --docs-addon-origins https://addon.feishu.cn \
  --feishu-ai-id <你的 AI 实例 id>
```

`--docs-addon-origins` 是**精确匹配**的 Origin 白名单, 可重复传多个。不支持通配符, 也不做
后缀匹配 —— `*` 等于让互联网上任意页面驱动你的 agent, 而 `endswith(".feishu.cn")` 会被
`evil-feishu.cn` 绕过。本地 `npm start` 调试时把 dev server 的 Origin (默认
`http://localhost:8080`) 也加进来。

把生成的 token 记下来, 稍后填进小组件设置面板。

> 监听 `0.0.0.0` 意味着同网段可访问。若网关要暴露到公网, 请放在反向代理后并启用 https;
> token 是唯一的门。

## 二、装工具链并上传程序包

**这一步是后台「小组件版本 / 更新类型」两个下拉框可选的前提** —— 版本列表是从已上传的
程序包里读出来的, 没上传过任何包时列表为空, 两个必填项因此置灰。

```bash
npm uninstall @bdeefe/opdev-cli -g     # 装过旧版才需要
npm install @lark-opdev/cli@latest -g
opdev login                            # 选 Feishu 开发环境, 浏览器登录
```

三个容易卡住的点:

- Node 官方要求 **`<= 18.20.8`**;
- 全局 `opdev >= 3.3.0` 必须配项目内 `@lark-opdev/block-docs-addon-webpack-utils >= 1.0.0`,
  版本错配时控制台报的是 `get lark session`;
- **小组件不支持在飞书测试企业里创建和调试**。

然后在本目录:

```bash
npm install
npm start        # 本地调试
npm run upload   # 上传程序包
```

上传前把 `app.json` 里的 `appID` 换成你的真实 App ID。`blockTypeID` 已填为
`blk_6a606854d7004bb97110da1a`。

## 三、回开发者后台补必填项

1. **小组件版本** 选刚上传的程序包, **更新类型** 按需选;
2. 基础信息: 图标 (240×240 PNG)、名称、介绍;
3. **安全设置 → 服务器域名白名单** 填网关域名 —— 漏了小组件会因 CSP 拦截而不工作;
4. **权限管理 → 云文档** 勾 `docx:document` 与 `docx:document:readonly`, 身份选
   `user_access_token`;
5. 创建版本发布并提交审核。

安全配置生效依赖发版+审核, 不是即时的, 记得留出审核时间。

## 四、在文档里使用

文档中 `/` 或 `+` 唤出小组件 → 首次打开点「设置」填网关地址与令牌 → 提问。每条回答下方有
「插入文档」按钮, 走 `Document.insertBlocksByMarkdown`, 标题/列表/代码块会落成真正的原生块。
文档处于只读模式或你没有编辑权时该按钮会禁用并说明原因。

## 开发

```bash
npm test          # 纯逻辑单测 (SSE 分块解析 / 设置读写), 无需浏览器
npm run build
```

`npm test` 重点覆盖两个错了很难发现的地方: 网关的一条 SSE 事件可能被 TCP 切在任意字节,
以及 `: keepalive` 注释不能被当成正文 (否则长回答里会夹进 "keepalive" 字样)。

## 文件

| 文件 | 作用 |
|---|---|
| `app.json` | 小组件配置: `appType: docs-addon`、`blockTypeID`、`contributes.addPanel` |
| `src/main.js` | SDK 初始化、UI、插入文档 |
| `src/gateway.js` | 调 `/docs-addon/chat`, 把 HTTP 状态翻成可操作的中文 |
| `src/sse.js` | SSE 解析 (与 Gateway SPA 的 `useSSE.js` 同一套格式) |
| `src/settings.js` | 后端地址/令牌的读写与归一化, 存在 Record 里 |
