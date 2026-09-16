import { useEffect, useState } from "react";

/**
 * 指向顶部「当前任务状态区」的非阻塞提示 —— 首次使用时弹一次。
 *
 * 与 ToC 的差别有三处, 都是**适配**不是删减:
 * * **锚点**: ToC 找 ``.quick-actions .agent-status-tooltip-wrap``, 这里的顶栏是
 *   ``.cend2-quick``(见 ``chat-topbar.tsx``), 状态按钮各自带上了
 *   ``agent-status-tooltip-wrap`` —— 取全部匹配元素的并集, 于是提示框框住的是整片状态区。
 * * **文案**: ToC 走 i18n(``statusTip.*``), 这里直接写中文 —— ToB 网页应用是单语言,
 *   为三条文案引入整套 i18n 不划算(真要三语言是另一件事)。
 * * **少一个指示器**: C 端那里是「时钟(思考状态) + 圆点(执行状态) + 宝箱(新交付物)」三个,
 *   而 ToB 这两个都由同一个 ``sending`` 驱动 —— 同时亮起、同时熄灭, 摆两个只会让人以为
 *   其中一个坏了(实测反馈「有点重复了」)。所以去掉了时钟, 只留圆点, 文案也跟着改成两个。
 *
 * 用 ``position: fixed`` + ``getBoundingClientRect`` 而不是 DOM 里挂父子关系: 顶栏会在
 * 流式期间频繁重渲染, 挂进去等于让提示跟着重排。
 */
type TargetRect = {
  left: number;
  top: number;
  width: number;
  height: number;
  bottom: number;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function TaskStatusTip({ onClose }: { onClose: () => void }) {
  const [rect, setRect] = useState<TargetRect | null>(null);

  useEffect(() => {
    const update = () => {
      const icons = Array.from(
        document.querySelectorAll(".cend2-quick .agent-status-tooltip-wrap"),
      );
      if (icons.length === 0) {
        // 顶栏还没挂出来(或用户在任务总览页): 不显示, 而不是锚在左上角乱飘。
        setRect(null);
        return;
      }
      const rects = icons.map((el) => el.getBoundingClientRect());
      const left = Math.min(...rects.map((r) => r.left));
      const top = Math.min(...rects.map((r) => r.top));
      const right = Math.max(...rects.map((r) => r.right));
      const bottom = Math.max(...rects.map((r) => r.bottom));
      setRect({ left, top, width: right - left, height: bottom - top, bottom });
    };
    update();
    // 顶栏是流式期间才长出来的, 所以前 5 秒轮询补一次位置; 之后靠 resize/scroll。
    const retry = window.setInterval(update, 250);
    const timer = window.setTimeout(() => window.clearInterval(retry), 5000);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.clearInterval(retry);
      window.clearTimeout(timer);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, []);

  if (!rect) return null;

  const tooltipLeft = clamp(rect.left + rect.width / 2 - 166, 12, Math.max(12, window.innerWidth - 356));
  const arrowLeft = clamp(rect.left + rect.width / 2 - tooltipLeft - 9, 14, 300);

  return (
    <>
      <span
        className="task-status-tip-frame"
        style={{ left: rect.left - 6, top: rect.top - 6, width: rect.width + 12, height: rect.height + 12 }}
      />
      <div
        className="task-status-tip"
        role="tooltip"
        style={{ left: tooltipLeft, top: rect.bottom + 10 }}
      >
        <span className="task-status-tip-arrow" style={{ left: arrowLeft }} />
        <h3>当前任务状态区</h3>
        <p>圆点标识 Agent 是否在干活，右端宝箱标识当前任务的新交付物。</p>
        <button type="button" onClick={onClose}>知道了</button>
      </div>
    </>
  );
}
