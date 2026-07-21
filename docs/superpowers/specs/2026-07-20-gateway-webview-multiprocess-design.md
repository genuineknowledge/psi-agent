# Gateway webview 多进程 + 异步消息架构

## 概述

将当前的 `GatewayWebView` 从"daemon 线程"模式重构为"多进程 Actor"模式，解决 pywebview 必须运行在主线程的限制。同时将 webview、tray 和 Gateway 主逻辑之间的耦合从 `threading.Event` / `GatewayWebView.show()` 直接引用解耦为异步消息队列。

## 当前问题

`_webview.py` 在 daemon 线程中调用 `webview.start()`，违反 pywebview 明确要求：

> GUI loop is expected to run on a main thread.
> — pywebview FAQ

当前 Gateway、webview、tray 三者通过底层原语直接互相调用：

```
Gateway.run()
  ├→ wv.show 指针直接传给 tray（GatewayTray.__init__(on_open=wv.show)）
  ├→ tray.start 和 wv.start 启动顺序耦合（要先启 wv 才能拿到 show 引用）
  ├→ wv.is_running() 守卫（webview 启动失败时 tray on_open 回退到浏览器）
  ├→ wv.wait_closed() / tray.wait_stop() 三路 if-elif 分支
  └→ wv.stop() / tray.stop() 清理顺序耦合

_webview.py:
  ├→ _on_closing() 里 if self._has_tray 分支（通过构造参数反向了解外部状态）
  └→ threading.Event + thread.is_alive() 手动管理

_tray.py:
  ├→ on_open 回调（既可能是 wv.show 也可能是 _open_browser）
  └→ _stop_event + wait_stop() 阻塞等待
```

**问题**：`_on_closing()` 要知道 tray 是否存在，`GatewayTray` 要知道 webview 是否存在，`Gateway.run()` 要了解两者的内部状态（`is_running()`）并手动编排三路等待。

## 目标架构

三个 Actor，各自封装自己的实现细节，仅通过异步消息队列对外暴露接口：

```
┌─────────────────────────────────────────────────────┐
│ Gateway (纯 async event loop)                        │
│                                                      │
│   async for evt in merge(wv.events, tray.events):    │
│     match evt:                                       │
│       "wv.closed" → break                            │
│       "tray.open" → await wv.send("show")            │
│       "tray.quit" → await wv.send("destroy"); break  │
│                                                      │
│  每个组件对外只暴露:                                   │
│    .events   → MemoryObjectReceiveStream[str]        │
│    .send(cmd) → async fire-and-forget                 │
└───────────┬──────────────────────────────────────────┘
            │
    ┌───────┴───────┐
    │               │
┌───┴────────┐  ┌──┴──────────────┐
│ WebViewProcess│  │ GatewayTray    │
│ (子进程)      │  │ (独立线程)      │
│              │  │                │
│ inbox:       │  │ _on_open:      │
│  "show"      │  │  q.put("open") │
│  "destroy"   │  │                │
│  "flash"     │  │ _quit:         │
│              │  │  q.put("quit") │
│ out events:  │  │                │
│  "ready"     │  │ pump task:     │
│  "hidden"    │  │  读 q → Memory │
│  "closed"    │  │  ObjectStream  │
└──────────────┘  └────────────────┘
```

**关键原则**：
- Gateway 不知道 webview 是子进程，tray 是线程——只管收发消息
- webview 子进程不 import psi_agent——纯 pywebview + multiprocessing
- tray 的 pystray 回调通过 threading.Queue → 后台 async task → MemoryObjectStream 桥接
- webview 子进程通过 mp.Queue（父→子命令）+ mp.Queue（子→父事件）桥接

## 消息协议

```
父 ──→ 子 webview:  "show" | "destroy" | "flash"
子 webview ──→ 父:  "ready" | "hidden" | "closed"
tray ──→ Gateway:   "open" | "quit"
```

## 文件变更清单

| 文件 | 操作 | 责任 |
|------|------|------|
| `src/psi_agent/gateway/_webview_main.py` | **新建** | webview 子进程入口，纯 pywebview + multiprocessing |
| `src/psi_agent/gateway/_webview.py` | **重写** | WebViewProcess 父进程 wrapper，封装 mp.Process + anyio 流桥接 |
| `src/psi_agent/gateway/_tray.py` | **修改** | 增加 async events 流（threading.Queue → anyio MemoryObjectStream） |
| `src/psi_agent/gateway/__init__.py` | **修改** | 简化 Gateway.run() 为事件驱动 merge 循环 |
| `src/psi_agent/gateway/_attention.py` | **修改** | AttentionHub 适配新接口（webview.flash → wv.send("flash")） |

## `_webview_main.py` 子进程逻辑

```python
# 无 psi_agent import，只 import webview / multiprocessing / threading / sys
# 主函数: webview_main(url, icon, tray_mode, app_name, cmd_q, evt_q)
#   window = webview.create_window(app_name, url)
#   window.events.closing:
#     有 tray → evt_q.put("hidden"); window.hide(); return False
#     无 tray → evt_q.put("closed"); return True
#   daemon 线程读 cmd_q: "show" → window.show(); "destroy" → window.destroy()
#   evt_q.put("ready"); webview.start(icon=icon)  # 主线程阻塞
```

## `_webview.py` WebViewProcess wrapper

```python
class WebViewProcess:
    __init__(url, icon, tray_mode, app_name):
        mp.Queue 命令队列、事件队列
        anyio MemoryObjectStream pair（接收子进程事件）
    async start():  spawn Process，启后台 task 泵 mp.Queue → MemoryObjectStream，等 "ready"
    async send(cmd):  cmd_q.put(cmd)
    async stop():     cmd_q.put("destroy"); Process.join(timeout=2)
    events → MemoryObjectReceiveStream
    is_alive() → Process.is_alive()
```

## `_tray.py` 变更

```python
class GatewayTray:
    __init__ 不再接受 on_open 参数
    events → MemoryObjectReceiveStream（事件流）
    start(): 启动 pystray daemon 线程 + 后台 async task（泵 threading.Queue → MemoryObjectStream）
      _on_open: self._q.put("open")
      _quit:   self._q.put("quit")
    stop(): 不变
    request_attention(): 不变
```

## Gateway.run() 简化后

```python
async for evt in merge(wv.events, tray.events):
    match evt:
        case "wv.closed":   break if not tray else None  # 无 tray 时关闭即退出
        case "tray.open":   await wv.send("show")
        case "tray.quit":   await wv.send("destroy"); break
finally:
    tray.stop() if tray else None
    await wv.stop()
```

不再有 `_on_closing` 里的 `self._has_tray` 判断、`is_running()` 守卫、`threading.Event` 手动设。

## 不涉及

- REST server / AI / Session / Title 管理不受影响
- `_attention.py` 的 AttentionHub 对 webview 的调用从 `wv.request_attention()` 改为 `wv.send("flash")`
- tray 的 `request_attention()` 保持线程内原地执行，不经过事件流
- SPA 前端无改动
