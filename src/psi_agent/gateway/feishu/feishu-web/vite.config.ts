import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// ToB 前端的构建配置。骨架期的三项能力 (能构建 / 能起 dev server / 能连本机 gateway)
// 原样保留, 只把业务用到的端点加进 proxy 表。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const gateway = (env.GATEWAY_ORIGIN || 'http://127.0.0.1:8765').replace(/\/+$/, '')

  return {
    plugins: [react()],
    // 与后端 add_static 的挂载前缀一致, 否则构建产物里的资源路径会 404。
    base: '/feishu-web/',
    build: {
      outDir: 'dist',
      assetsDir: 'assets',
    },
    server: {
      // 5174 已被 ToC 的 spa-v2 占用, ToB 用 5173。
      port: 5173,
      // **必须 true**。false(vite 的默认值)时端口被占**不报错**, 静默换到下一个空闲端口
      // (5173 → 5174 → 5175 ...), 而文档、书签、AGENTS.md 里写的都还是 5173。5173 上活着的
      // 那个**别的** dev server(最常见来源: 另一个 worktree 里忘关的 ``npm run dev`` ——
      // Windows 上关终端不一定收走 node 进程)继续应答: 页面能开、功能能用, 但改本 worktree
      // 的前端**永远看不到变化**, 因为你看的是另一棵树的源码。唯一线索是 vite 日志里
      // ``Port 5173 is in use, trying another one...`` 那行, 常被 npm 输出刷掉。
      // 表现与上面那个 proxy key 的坑几乎一样(都是「dev server 看着正常但改前端不生效」),
      // 成因却完全不同 —— 实测踩过, 见 ``test_feishu_web_dev_strict_port.py``。
      // true 让端口被占时**启动即失败**, 而不是静默错位到另一棵树。
      strictPort: true,
      // 必须显式写 127.0.0.1: vite 默认只监听 ``[::1]``, 于是验收里那个
      // ``http://127.0.0.1:5173`` 直接连不上 (curl 返回 000)，而日志打的是
      // ``localhost``, 看不出差别 —— 实测踩过。
      host: '127.0.0.1',
      proxy: {
        // 每一项都对应后端已存在的路由 (``gateway`` 下的 add_get/add_post/add_delete)。
        '/defaults': gateway,
        // **``/ais`` 有意不在这里。** 骨架确实有这条路由, 但本前端不该有「AI 列表」的概念:
        // 建会话挂哪个 AI 由后端 ``GET /feishu/defaults`` 给唯一答案(见 ``api.ts`` 里
        // ``getFeishuDefaultAiId``)。留着这条代理, 谁再写回一个 ``/ais`` 调用就能在 dev 里
        // 静默跑通, 于是「网页应用与机器人用不同模型」又回到靠纪律拦。删掉它, 那种调用在
        // 本地就直接不通。
        // ``/sessions/{id}/chat`` 是 SSE, 必须关掉缓冲否则流式变成一次性返回。
        '/sessions': { target: gateway, changeOrigin: true, ws: false },
        '/titles': gateway,
        '/summaries': gateway,
        '/workspace': gateway,
        // 注意**没有** ``/auth``: 飞书免登的三条已挪到 ``/feishu/auth/*`` 前缀下(裸
        // ``/auth/me`` ``/auth/logout`` 被 desktop 那条产品线占着, 同进程装配下先注册者
        // 胜出), 所以下面那条 ``/feishu`` 已经把免登一起代理了。
        // ``/feishu/*``(``_routes.py`` 里的 ``register_feishu_routes``): 免登三条、app-id、
        // 按身份过滤的 sessions/titles/summaries、``/feishu/route`` ``/feishu/routes`` 都是普通 JSON。
        // 聊天流式仍然打骨架的 ``/sessions/{id}/chat``(上面那条), ``/feishu`` 下**没有**注册
        // 任何 SSE 端点, 所以不需要 ``{ target, changeOrigin, ws: false }`` 的特殊处理。
        //
        // **必须是这条正则, 不能写成 ``'/feishu'``**: 字符串 key 在 vite 里是**前缀**匹配,
        // 而本应用的 ``base`` 恰好是 ``/feishu-web/`` —— 也以 ``/feishu`` 开头。写成字符串
        // 时整个前端路径连同 ``/@vite/client`` 一起被代理到 gateway, dev server 于是一行自己
        // 的东西都不服务: 打开 5173 拿到的是 gateway 里**上一次 build 的 dist**, 热更新永远
        // 不生效, 而且 ``/feishu-web/`` 带斜杠时 aiohttp 的 ``show_index=False`` 直接回 403。
        // 两个表现都不像「代理配错了」, 所以这个坑很能藏 —— 实测踩过。
        // ``^`` 开头的 key 被 vite 当正则, 负向前查把 ``-web`` 摘出去。
        '^/feishu(?!-web)': gateway,
      },
    },
  }
})
