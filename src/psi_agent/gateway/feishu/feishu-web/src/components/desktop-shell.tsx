import type { ReactNode } from "react";
import { ListTodo, MessageCircle, Settings } from "lucide-react";
import { brandMark } from "./brand";

export type ShellNav = "tasks" | "chat";

/**
 * 应用外壳: 海豚自己的任务/对话导航 + 内容区。真实部署在飞书客户端内运行,
 * 不再画一层模拟飞书桌面的顶栏和左侧图标栏。
 */
export function DesktopShell({
  nav,
  userName,
  onNavigate,
  children,
}: {
  nav: ShellNav;
  userName: string;
  onNavigate: (nav: ShellNav) => void;
  children: ReactNode;
}) {
  return (
    <div className="ht-desktop">
      <div className="ht-dt-app">
        <nav className="ht-dt-nav" aria-label="海豚应用导航">
          <div className="ht-dt-brand">
            {brandMark("sidebar")}
            <div>
              <strong>海豚 Agent</strong>
              <em>企业版 · 云服务器部署</em>
            </div>
          </div>
          <button
            type="button"
            className={nav === "tasks" ? "active" : ""}
            aria-current={nav === "tasks" ? "page" : undefined}
            onClick={() => onNavigate("tasks")}
          >
            <ListTodo size={16} />
            <span>任务总览</span>
          </button>
          <button
            type="button"
            className={nav === "chat" ? "active" : ""}
            aria-current={nav === "chat" ? "page" : undefined}
            onClick={() => onNavigate("chat")}
          >
            <MessageCircle size={16} />
            <span>对话</span>
          </button>
          <div className="ht-dt-nav-foot">
            <span className="ht-avatar">{userName.slice(0, 1) || "我"}</span>
            <span>{userName || "我"} · 管理员</span>
            <Settings size={15} />
          </div>
        </nav>
        <main className="ht-dt-main">{children}</main>
      </div>
    </div>
  );
}
