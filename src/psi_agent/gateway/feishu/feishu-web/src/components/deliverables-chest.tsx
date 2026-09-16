import { useMemo, useState } from "react";
import { Download, Loader2, X } from "lucide-react";
import { fetchDeliverable } from "../api";
import { saveBlob } from "../services/fileDownload";

/**
 * 宝箱 —— 全部会话的交付物集中在这里, 勾选后逐个下载。
 *
 * 与「新交付物」面板的分工: 那个是**待确认**的增量(本次流式里新收到的), 这个是**全部**
 * 交付物的存量视图(含历史)。两者数据源不同, 所以不是一个东西的两个皮肤。
 *
 * 下载**逐个取、逐个存**, 不打包 zip: 打包要引入 zip 依赖(jszip 在本项目里只是
 * docx-preview / pptx-preview 的传递依赖, 直接 import 它等于把别人的依赖树当自己的 API),
 * 而"勾几个下几个"已经满足需求。真要打包是另一个决定。
 */
export type ChestItem = {
  /** 所属会话 —— 下载路由要用它做归属校验。 */
  sessionId: string;
  sessionTitle: string;
  name: string;
  /** 绝对路径; 空串表示这条只有文件名、拿不到路径, 所以下不了(见 ``downloadable``)。 */
  path: string;
  /** 本次流式里新收到、还没确认过的。 */
  isNew?: boolean;
};

/**
 * 能不能下 —— 下载路由要求带绝对路径(后端拿它比对"本会话声明过的交付物"白名单)。
 * 只有文件名的那种(历史里 ``files[]`` 没带 path、流式事件也没带 path)在列表里留着,
 * 但点不动: 静默藏掉会让用户以为文件丢了。
 */
function downloadable(item: ChestItem): boolean {
  return Boolean(item.path);
}

export function DeliverablesChest({ items, onClose }: { items: ChestItem[]; onClose: () => void }) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");

  // 同名的两个文件可能分属不同会话, 所以键必须是「会话 + 路径」而不是文件名。
  const keyOf = (item: ChestItem) => `${item.sessionId}\u0000${item.path}`;

  const groups = useMemo(() => {
    const map = new Map<string, { title: string; items: ChestItem[] }>();
    for (const item of items) {
      const group = map.get(item.sessionId) || { title: item.sessionTitle, items: [] };
      group.items.push(item);
      map.set(item.sessionId, group);
    }
    return [...map.entries()];
  }, [items]);

  const toggle = (item: ChestItem) => {
    const key = keyOf(item);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const downloadOne = async (item: ChestItem) => {
    const blob = await fetchDeliverable(item.sessionId, item.path);
    saveBlob(blob, item.name);
  };

  const downloadSelected = async () => {
    const chosen = items.filter((item) => selected.has(keyOf(item)) && downloadable(item));
    if (!chosen.length) return;
    setBusy(true);
    setError("");
    let done = 0;
    try {
      for (const item of chosen) {
        setProgress(`${item.name}（${done + 1}/${chosen.length}）`);
        await downloadOne(item);
        done += 1;
        // 逐个触发之间留一点间隔: 同一瞬间连开多个下载, 部分浏览器会把后面的静默丢掉。
        await new Promise((resolve) => window.setTimeout(resolve, 300));
      }
      setProgress("");
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="chest-backdrop" role="dialog" aria-modal="true" aria-label="宝箱 · 全部交付物">
      <div className="chest-panel">
        <header className="chest-head">
          <div>
            <strong>宝箱</strong>
            <em>全部会话的交付物, 勾选后可下载（共 {items.length} 份）</em>
          </div>
          <button type="button" className="chest-close" aria-label="关闭" title="关闭" onClick={onClose}>
            <X size={16} />
          </button>
        </header>

        <div className="chest-body">
          {items.length === 0 && (
            <p className="chest-empty">
              还没有交付物。Agent 通过 [SEND:] 交出的文件会累积在这里，按会话分组。
            </p>
          )}
          {groups.map(([sessionId, group]) => (
            <section className="chest-group" key={sessionId}>
              <h4>{group.title}</h4>
              <ul>
                {group.items.map((item) => {
                  const key = keyOf(item);
                  const can = downloadable(item);
                  return (
                    <li key={key} className={`${item.isNew ? "is-new" : ""}${can ? "" : " is-untracked"}`}>
                      <label title={can ? item.path : "这条只有文件名, 没有可下载的路径记录"}>
                        <input
                          type="checkbox"
                          checked={selected.has(key)}
                          onChange={() => toggle(item)}
                          disabled={busy || !can}
                        />
                        <span className="chest-file-name">{item.name}</span>
                        {item.isNew && <span className="chest-new-badge">新</span>}
                      </label>
                      <button
                        type="button"
                        className="chest-one"
                        title={can ? `下载 ${item.name}` : "没有可下载的路径记录"}
                        aria-label={`下载 ${item.name}`}
                        disabled={busy || !can}
                        onClick={() => void downloadOne(item).catch((e) => setError(String(e)))}
                      >
                        <Download size={13} />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>

        {error && <div className="chest-error" role="alert">{error}</div>}

        <footer className="chest-foot">
          <button
            type="button"
            className="ht-btn"
            disabled={busy || items.length === 0}
            onClick={() => setSelected(new Set(items.filter(downloadable).map(keyOf)))}
          >
            全选
          </button>
          <button type="button" className="ht-btn" disabled={busy || !selected.size} onClick={() => setSelected(new Set())}>
            清空
          </button>
          <span className="chest-progress">{progress}</span>
          <button
            type="button"
            className="ht-btn primary"
            disabled={busy || !selected.size}
            onClick={() => void downloadSelected()}
          >
            {busy ? <Loader2 size={14} className="chest-spin" /> : <Download size={14} />}
            下载所选（{selected.size}）
          </button>
        </footer>
      </div>
    </div>
  );
}
