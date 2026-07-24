# HaiTun Agent CSS 架构

## 1. 级联合同

`app/globals.css` 目前按以下顺序形成视觉结果：基础 Token 与 Reset → 侧栏与基础控件 → 卡片与对话 → 抽屉与交付物 → 新建任务与模板 → 动效与响应式 → 蓝色品牌覆盖 → 搜索/设置/聚焦工作区的后置规则。

文件后半段存在有意的后置覆盖。修改前半段同名选择器可能不会生效，因为后置蓝色主题或响应式规则会再次覆盖。交付阶段保持单文件和原始顺序，避免拆文件时改变视觉结果；未来拆分必须只按连续区间机械迁移，并让入口按数字顺序导入。

## 2. 核心 Token

```css
--brand-rgb: 0, 123, 255;
--brand-500: #007bff;
--brand-400: #2e9eff;
--brand-600: #006ae6;
--brand-700: #0057bd;
--navy-950: #06172b;
--canvas: #f2f7fd;
--coral: #ff6b57;
--gold: #d9a62c;
--success: #27a06b;
```

`--brand-rgb` 使用逗号元组，以兼容现有 `rgba(var(--brand-rgb), alpha)`，不能改为空格元组。

组件动态变量：

- `--task-accent`：任务卡内联强调色。
- `--progress`：进度环角度。
- `--focus-height`：聚焦工作区高度。
- `--coin-*`、`--cx/cy/cr/cd`：宝箱和庆祝金币轨迹。

## 3. 响应式合同

| 条件 | 布局含义 |
| --- | --- |
| `> 1040px` | 标准桌面，侧栏 292px |
| `881–1040px` | 窄桌面/平板横屏，侧栏可缩至 260px |
| `<= 880px` | 移动端侧栏抽屉与上下任务工作区 |
| `<= 420px` 且高度 `<= 740px` | 短小手机压缩布局 |
| 高度 `<= 760px` 且宽度 `>= 881px` | 短屏桌面压缩卡片与间距 |
| `@container (max-width: 820px)` | 按主舞台可用宽度切换聚焦布局 |

容器断点和移动端媒体查询可能同时命中；移动端规则依靠更靠后的源码位置覆盖容器规则，不能交换顺序。

## 4. 动效时序

| 动效 | CSS/前端时序 |
| --- | --- |
| 卡片离场 | 260ms |
| 卡片入场 | 55ms 延迟 + 410ms |
| React 清理旧层 | 470ms |
| 聚焦卡片形变 | 420ms |
| 宝箱打开后拉起抽屉 | 430ms |
| 保存成果庆祝 | 820ms |

React 定时器与 CSS 动画存在时序耦合；调整任一数值时必须同步修改另一侧并执行真实浏览器回归。`prefers-reduced-motion` 需要同时约束 CSS 动画和 JavaScript 延时。

## 5. 组件归属

- `ProgressRing`、`TreasureVisual`、`StatusPill`：基础控件。
- `Sidebar`、`TaskRow`、全局搜索、设置：应用壳层。
- `OverviewCard`、`TaskCard`、紧凑卡：任务卡区域。
- `TaskFocusDetails`：聚焦工作区。
- `ArtifactDrawer`、`InboxDrawer`、金币庆祝：浮层。
- `NewTaskWorkspace`、`TemplateLibrary`：次级主页面。

暂不迁移 CSS Modules。现有动态父子选择器、全局状态选择器和跨组件响应规则较多，改类名会扩大回归面。
