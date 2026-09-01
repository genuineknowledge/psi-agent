/// <reference types="vite/client" />

/**
 * 飞书 JSSDK 的全局对象 —— 由 ``index.html`` 里那个同步 script 注入。
 *
 * 两个 App ID 参数**拼法不同**, 是官方文档里就有的不一致, 不是笔误:
 * ``requestAccess`` 用 ``appID``, ``requestAuthCode`` 用 ``appId``。写错的表现是
 * 「网页应用必传 appID」类报错, 而非静默失败。
 */
interface FeishuRequestAccessArgs {
  appID: string;
  scopeList: string[];
  success: (res: { code: string }) => void;
  fail: (err: { errno?: number; errString?: string }) => void;
}

interface FeishuRequestAuthCodeArgs {
  appId: string;
  success: (res: { code: string }) => void;
  fail: (err: { errno?: number; errString?: string }) => void;
}

interface Window {
  h5sdk?: {
    ready: (cb: () => void) => void;
    error?: (cb: (err: unknown) => void) => void;
  };
  tt?: {
    requestAccess?: (args: FeishuRequestAccessArgs) => void;
    requestAuthCode?: (args: FeishuRequestAuthCodeArgs) => void;
  };
}
