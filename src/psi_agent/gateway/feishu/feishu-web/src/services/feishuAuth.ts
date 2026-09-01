/**
 * 飞书网页应用免登 —— 拿一次性 ``code`` 交给后端。
 *
 * 严格照官方文档 ``client-docs/h5/development-guide/step-3`` 的兼容示例实现, 两级退路:
 *
 * 1. ``window.tt.requestAccess`` 不存在 → JSSDK 版本过低, 用 ``requestAuthCode``。
 * 2. ``requestAccess`` 的 ``fail`` 里 ``errno === 103`` → 飞书客户端版本过低, 同样退到
 *    ``requestAuthCode``; 其余 errno 是用户拒绝或真失败, 直接报错。
 *
 * **不做静默回退到假身份。** 在飞书客户端外 ``window.h5sdk`` 不存在, 这时抛
 * ``FeishuAuthUnavailable``, 由 UI 显示「请在飞书客户端内打开」+ 重试按钮。历史教训:
 * PR 755 的免登分支永不执行(它引的 SDK 不存在), 每次都掉进写死了一个真实 open_id 的
 * fallback, 上云后所有访问者都是同一个人。
 *
 * ``FeishuAuthUnavailable`` 是**调用方的分支判据**
 * ------------------------------------------------
 * ``useAuth`` 只在收到这个类型时才去试后端的开发旁路。所以「拿不到 code 的能力不可用」
 * 一律用这个类型, 而「能力在但这一次失败了」(用户拒绝授权、code 过期)用普通 ``Error``
 * —— 后者重试有意义, 前者重试也没用。抛错顺序同样吃劲, 见 ``requestFeishuCode``。
 *
 * ``code`` 有效期 3 分钟且只能用一次 —— 所以每次登录都重新取, 绝不缓存。
 */

/** SDK 不在(不在飞书客户端内)。与「取 code 失败」分开, UI 的提示文案不同。 */
export class FeishuAuthUnavailable extends Error {}

const READY_TIMEOUT_MS = 10_000;

/** 等 ``h5sdk.ready``; 超时也算不可用 —— 否则页面会永远停在 loading。 */
function sdkReady(): Promise<void> {
  const sdk = window.h5sdk;
  if (!sdk) {
    return Promise.reject(new FeishuAuthUnavailable("window.h5sdk 不存在, 请在飞书客户端内打开"));
  }
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(
      () => reject(new FeishuAuthUnavailable("飞书 JSSDK 初始化超时")),
      READY_TIMEOUT_MS,
    );
    sdk.ready(() => {
      window.clearTimeout(timer);
      resolve();
    });
  });
}

function viaRequestAuthCode(appId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const fn = window.tt?.requestAuthCode;
    if (!fn) {
      reject(new FeishuAuthUnavailable("JSSDK 不支持 requestAuthCode"));
      return;
    }
    // 注意: 这里是小写 ``appId``, 与 requestAccess 的 ``appID`` 不同。
    fn({
      appId,
      success: (res) => resolve(res.code),
      fail: (err) => reject(new Error(`requestAuthCode 失败 (errno=${err.errno ?? "?"}): ${err.errString ?? ""}`)),
    });
  });
}

/** 取一次性登录 code。*appId* 由后端 ``GET /feishu/app-id`` 提供, 不写死。 */
export async function requestFeishuCode(appId: string): Promise<string> {
  // ``sdkReady()`` **必须先于** app_id 检查。两个理由:
  //
  // 1. 顺序上「有没有 JSAPI」比「后端配没配」更根本 —— 不在飞书客户端内时, app_id 配得
  //    再全也拿不到 code, 报「后端未配置 App ID」是指错了地方。
  // 2. 反过来写会让**本地开发的默认组合**(app_id 为空 + 不在飞书客户端内)抛普通
  //    ``Error``, 于是调用方 ``useAuth`` 的 ``err instanceof FeishuAuthUnavailable`` 判假,
  //    开发旁路整段被跳过, 页面停在「登录失败: 后端未配置飞书 App ID, 无法免登」。实测
  //    踩过, 而且只有这一个组合会踩(app_id 非空时抛的就是 ``FeishuAuthUnavailable``,
  //    类型对), 所以它藏得住 —— 别再把这两行换回去。
  await sdkReady();
  if (!appId) throw new Error("后端未配置飞书 App ID, 无法免登");
  const requestAccess = window.tt?.requestAccess;
  if (!requestAccess) return viaRequestAuthCode(appId); // JSSDK 版本过低

  return new Promise<string>((resolve, reject) => {
    // 注意: 这里是大写 ``appID``, 网页应用必传。空 scopeList = 只要用户凭证信息。
    requestAccess({
      appID: appId,
      scopeList: [],
      success: (res) => resolve(res.code),
      fail: (err) => {
        if (err.errno === 103) {
          // 客户端版本过低, 不支持 requestAccess。
          viaRequestAuthCode(appId).then(resolve, reject);
          return;
        }
        reject(new Error(`requestAccess 失败 (errno=${err.errno ?? "?"}): ${err.errString ?? ""}`));
      },
    });
  });
}
