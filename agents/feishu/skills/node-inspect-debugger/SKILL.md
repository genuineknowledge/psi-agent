---
name: node-inspect-debugger
description: "调试 Node.js 程序 —— 用 Node 自带的 `--inspect` / `node inspect` 走 Chrome DevTools 协议(CDP)。LOAD whenever 用户要:调试一段 Node/JS 脚本、查某个变量/表达式在运行到某行时的值、定位一个只在运行时才暴露的 bug、看调用栈、断点取值、attach 到一个已在跑的 Node 进程(--inspect / -p <pid>)、或拿到 CDP 的 WebSocket 端点交给 Chrome DevTools。核心是 `node inspect --probe <file>:<line> --expr <expr> --json` 非交互探针模式:不进交互 REPL,一条命令在指定源码位置求值并打印结果,最适合 agent。全部通过 bash 工具跑本机 `node`,不新增 Python 依赖,不改 pyproject / nuitka / pyinstaller。NOT for 浏览器前端 JS 调试(那是页面里的 DevTools),也 NOT for 装 node/nvm 本身。"
category: coding
---

# Node.js 调试 —— `--inspect` + Chrome DevTools 协议(via `node inspect`)

用 **Node 自带**的检查器调试 Node.js 程序:Node 通过 `--inspect` 系列开关暴露一个
**Chrome DevTools 协议(CDP)** 端点,而随 node 一起发布的 **`node inspect`** 子命令本身
就是一个 CDP 客户端。agent 只需用 **bash** 工具跑本机 `node`,**不封装 Python tool、不新增
依赖、不改 pyproject / nuitka / pyinstaller**——和 `codex`、`himalaya`、`huggingface-hub`
等 CLI-wrapper skill 一样。

默认用**中文**回答,除非用户明显用别的语言。

## 何时使用

- 用户给一段 Node/JS 脚本要你「调试一下 / 看看为什么结果不对」。
- 想知道**运行到某一行时某个变量/表达式的值**(比 `console.log` 灌一堆日志干净)。
- bug 只在运行时暴露,需要在真实执行路径上取值、看对象结构、看调用栈。
- 要 **attach 到一个已经在跑的 Node 进程**(服务、脚本)去查状态。
- 要拿到 **CDP 的 WebSocket 端点**,交给 Chrome DevTools / VS Code 图形化调试。

**不适用**:浏览器页面里的前端 JS(用页面自带 DevTools);安装 node / nvm 本身;
纯静态代码审查(直接读代码即可,不必起调试器)。

## 首选路径:`--probe` 非交互探针模式(agent 就用这个)

新版 Node(≥ v22 起,本机实测 v26)的 `node inspect` 带一个**非交互**模式:在指定源码
位置放「探针」,每次执行到那里就求值并打印,然后自己退出。**不进交互 REPL,一条 bash 命令
搞定**,是 agent 最该用的方式。

```bash
node inspect \
  --probe app.js:10   --expr "user" \
  --probe utils.js:5  --expr "config.options" \
  --json --preview --timeout 15000 \
  -- app.js --arg-for-app=foo
```

- `--probe <file>:<line>[:<col>]` 后面**必须紧跟**一个 `--expr <expr>`,成对出现。
- `<line>` / `<col>` 是 **1-based**。省略列时探针绑到该行**第一个可执行列**。
- 同一位置可放多个 `--probe`(共享一次暂停与作用域),`--expr` 按命令行顺序求值。
- `--json` 输出机器可读 JSON;`--preview` 额外带 V8 对象预览(能看到对象/数组的字段)。
- `--timeout <ms>` 全局超时(默认 30000)。给可能卡住的脚本设小一点,别让 bash 干等。
- `--` 之后才是**传给被调试脚本的 Node 旗标 / 参数**。
- probe 进程**正常退出码 0**(除非它自己出错);被调试目标的错误会作为一条终端
  `error` 事件出现在报告里,而不是让进程崩。

读结果:JSON 里 `results` 是事件数组。命中是 `{"event":"hit", "result":{...}}`,
`result.value` / `result.preview.properties` 里是值;没命中是 `{"event":"miss"}`;
最后通常有 `{"event":"completed"}`。

## ⚠️ Windows 路径匹配坑(最容易踩)

`--probe` 的 `<file>` 是**按路径后缀匹配 Node 实际加载的 script URL**(以路径分隔符锚定),
**不是**你传给命令的那个路径字符串。在 Windows / Git-Bash 上:

- 传 `--probe /tmp/foo.js:3` 往往**全部 miss** —— 因为 Node 加载的 URL 是
  `file:///C:/Users/.../Temp/foo.js`,POSIX 的 `/tmp/...` 后缀对不上。
- **对策:用裸文件名**做后缀,例如 `--probe foo.js:3`,几乎总能命中。
- 若同名文件多份、需要收窄,用**真实 Windows 路径的后缀片段**:
  先 `cygpath -w /tmp/foo.js` 拿到 `C:\Users\...\foo.js`,再取一段尾巴(如 `Temp/foo.js`,
  正斜杠即可)当 `--probe` 的 file。
- 命中的行要对准**真实源码行号**;若某行 miss,试相邻可执行行(声明行有时绑不上,
  见下条)。

**探针别放在 `let`/`const` 变量的声明行本身**去读它,可能抛 `ReferenceError`(TDZ)。
要读某个 `const x = ...`,把探针放在**它之后**的一行。

## 传统路径:`--inspect` + CDP 端点(需要图形化 / attach 时)

起一个带检查器的 Node 进程,拿到 CDP 的 WebSocket 端点:

```bash
# --inspect-brk 会在首行断住等 debugger;--inspect 则正常跑
node --inspect-brk=127.0.0.1:9229 app.js &
sleep 1
# 拿 CDP 端点(webSocketDebuggerUrl 就是 CDP ws 地址)
curl -s http://127.0.0.1:9229/json/list
```

- `--inspect[=host:port]`:正常运行同时开检查器(默认 `127.0.0.1:9229`)。
- `--inspect-brk`:在用户代码第一行**断住**,等客户端连上再继续(适合从头调试)。
- `--inspect-wait`:启动即等 debugger 连上才开始跑(不一定在首行断)。
- **端口用满则从 9229 递增**;多进程时指定不同端口或用 `:0` 让系统随机分配。
- `curl http://host:port/json/list` 返回里的 `webSocketDebuggerUrl`(`ws://...`)就是
  CDP 端点 —— 可交给 Chrome(`chrome://inspect`)、VS Code 或任何 CDP 客户端。
- **安全**:检查器端口 = 任意本地代码执行入口。**只绑 `127.0.0.1`,绝不绑 `0.0.0.0` /
  公网**;调完**及时结束进程**关掉端口。

### attach 到已在跑的进程

```bash
node inspect -p <pid>              # 按 PID attach(向目标发信号开检查器)
node inspect 127.0.0.1:9229        # 连已开 --inspect 的端点
```

配合 `--probe`/`--expr` 也可以对 attach 的目标做非交互取值。

## ⚠️ 别在一次性 bash 里跑交互式 `node inspect` REPL

不带 `--probe` 直接 `node inspect app.js` 会进**交互式调试 REPL**(`cont`/`next`/`step`/
`repl`/`exec` 等命令)。它需要 TTY 实时会话,**在 agent 的一次性、非交互 bash 里会挂死/超时**
(实测用 heredoc 喂命令会卡到超时)。

- **优先用 `--probe` 非交互模式**回答「某处的值是多少」这类问题。
- 需要人来交互式单步时,把命令**给用户在自己终端里跑**,别在 bash 工具里起 REPL。
- 若非要脚本化交互,考虑用 `--inspect` 起端点 + 一个真正的 CDP 客户端,而不是喂 REPL。

## 收尾

- 后台起的 `node --inspect*` 进程**用完必须结束**(记下 PID `kill`,或 Windows 上
  `taskkill //F //PID <pid>`)。**不要**无差别 `taskkill //F //IM node.exe`——会误杀用户
  其它 node 程序。
- 临时调试脚本、日志文件调完清理掉。

## TypeScript / 转译代码

被调试的是**实际运行的 JS**。若跑的是 `ts-node`/编译产物,`--probe` 的行号要对准**运行时
真正加载的文件**(编译后的 `.js`,或有 source map 时 Node 加载的那个 URL),不是你手写的
`.ts` 源码行。拿不准时先 `--inspect` 起端点、看 `/json/list` 里的 `url` 确认加载的是哪个文件。
