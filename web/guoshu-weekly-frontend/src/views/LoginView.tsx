import { useState } from "react";
import { login } from "../api";

/** Trial-stage login (plan 6.1 login view). Registration comes with B5. */
export function LoginView({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [username, setUsername] = useState("demo");
  const [password, setPassword] = useState("demo");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const ok = await login(username, password);
    setBusy(false);
    if (ok) {
      onLoggedIn();
    } else {
      setError("登录失败,请检查账号密码");
    }
  }

  return (
    <div className="loginShell">
      <form className="loginCard" onSubmit={submit}>
        <h1>国数周报 Agent</h1>
        <p>演示环境,数据为模拟周报,非集团真实周报</p>
        <label>账号</label>
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        <label>密码</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        <button type="submit" disabled={busy}>{busy ? "登录中…" : "登录"}</button>
        {error && <div className="loginError">{error}</div>}
      </form>
    </div>
  );
}
