import { useCallback, useState } from "react";
import { logout } from "./api";
import { ChatView } from "./views/ChatView";
import { HistoryView } from "./views/HistoryView";
import { LoginView } from "./views/LoginView";
import { ReportView } from "./views/ReportView";
import "./styles.css";

type View = "chat" | "history" | "report";

/**
 * Four-view shell (plan 6.1): chat is the main view; history and report are
 * placeholders until their B5 content lands; login is a separate screen.
 */
export default function App() {
  const [loggedIn, setLoggedIn] = useState(false);
  const [view, setView] = useState<View>("chat");

  const handleExpired = useCallback(() => setLoggedIn(false), []);

  async function handleLogout() {
    await logout();
    setLoggedIn(false);
  }

  if (!loggedIn) {
    return <LoginView onLoggedIn={() => setLoggedIn(true)} />;
  }

  return (
    <div className="appShell">
      <header className="topbar">
        <div className="brand">
          <div className="brandMark">周</div>
          <div>
            <strong>国数周报 Agent</strong>
            <small>基于正式周报数据的对话问答</small>
          </div>
        </div>
        <div className="topMeta">
          <span className="demoBanner">演示数据,非集团真实周报</span>
          <nav className="navTabs">
            <button className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}>对话</button>
            <button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>历史</button>
            <button className={view === "report" ? "active" : ""} onClick={() => setView("report")}>报告</button>
          </nav>
          <button className="logoutButton" onClick={() => void handleLogout()}>退出</button>
        </div>
      </header>

      {view === "chat" && <ChatView onSessionExpired={handleExpired} />}
      {view === "history" && <HistoryView />}
      {view === "report" && <ReportView />}
    </div>
  );
}
