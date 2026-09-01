"""Runtime — AI / Session / Router 实例的注册表与生命周期管理。

这一层认识的最高层概念只有内核 (``session`` / ``ai`` / ``router`` / ``protocol`` /
``channel._core``): 它知道怎么 spawn 一个 Session、怎么解析 Router 的类型化依赖、
怎么把 JSONL 历史投影成前端能渲染的结构。它**不**认识任何接入形态 —— 没有网页界面、
没有飞书、没有桌面托盘、没有登录。因此 ``git grep "from psi_agent.gateway" --
src/psi_agent/runtime/`` 必须无输出, 这是这个包存在的意义所在。

反过来的依赖是允许且预期的: ``gateway`` 组装这些 manager 并把它们接到 REST + Web UI
上, ``gateway/feishu/_feishu_manager.py`` 复用 ``SessionManager`` 给每个飞书用户 spawn 独立
Session。方向单一 —— gateway → runtime, 永不回头。

命名: 与 ``session/runtime_context.py`` 只是字面相近, 两者在 Python 命名空间里互不
遮蔽 (一个是顶层包 ``psi_agent.runtime``, 一个是 ``psi_agent.session`` 下的模块),
全库 4 处 ``runtime_context`` 导入点也都是全限定写法。
"""
