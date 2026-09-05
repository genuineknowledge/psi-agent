import React, { Component } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

class ErrorBoundary extends Component<{ children: React.ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ height: "100vh", display: "grid", placeItems: "center", textAlign: "center", fontFamily: "system-ui", color: "#6b7480", padding: "24px" }}>
          <div>
            <p style={{ fontWeight: 600, color: "#1f2329", margin: "0 0 6px" }}>页面出现异常</p>
            <p style={{ fontSize: 13, margin: "0 0 14px" }}>{this.state.error.message || String(this.state.error)}</p>
            <button onClick={() => this.setState({ error: null })} style={{ padding: "8px 14px", border: 0, borderRadius: 8, background: "#3370ff", color: "#fff", cursor: "pointer" }}>重新加载</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
