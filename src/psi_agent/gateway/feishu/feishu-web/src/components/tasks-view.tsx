import { Check, Download, MessageCircle, Pin, Plus, Search, Trash2, Workflow } from "lucide-react";
import type { Task } from "../types";
import { statCell, statusPill } from "./brand";
import { TreasureVisual } from "./treasure";

export interface TasksViewProps {
  tasks: Task[];
  filtered: Task[];
  counts: Record<string, number>;
  /** 本月执行过的会话数 —— 由后端 `GET /feishu/stats/monthly` 一次算好, 前端只透传。 */
  monthlyRuns: number;
  selected?: Task;
  filter: string;
  search: string;
  onFilter: (f: string) => void;
  onSearch: (v: string) => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  /** 置顶/取消置顶 —— 纯前端偏好, 置顶的排在列表最前。 */
  onTogglePin: (id: string) => void;
  onOpenChat: (id: string) => void;
  onOpenNewDeliverables: () => void;
  newDeliveryCount: number;
  /** 打开宝箱: 全部会话的交付物, 里面挑着下载。 */
  onOpenChest: () => void;
  /** 导出对话历史: 勾选会话, 下载原始 jsonl。 */
  onExportHistory: () => void;
  onNewTask: () => void;
}

export function TasksView(props: TasksViewProps) {
  const { filtered, counts, monthlyRuns, selected, filter, search, onFilter, onSearch, onSelect, onDelete, onTogglePin, onOpenChat, onOpenNewDeliverables, newDeliveryCount, onOpenChest, onExportHistory, onNewTask } = props;
  const filters = [["all", "全部"], ["working", "进行中"], ["attention", "待处理"], ["done", "已完成"]] as const;
  const deliverableTotal = props.tasks.reduce((sum, t) => sum + (t.files?.length || 0), 0);
  return (
    <>
      <div className="ht-dt-head">
        <div><h2>任务总览</h2><p>跨群任务与交付物统一管理</p></div>
        <div className="ht-actions">
          {/*
            宝箱 = **全部交付物**, 与对话顶栏那颗是同一个图标 (``TreasureVisual``, ToC 那边
            也是它)。只给图标不给文字: 这一行右边已经有「新建任务」, 再加一个带字的按钮会把
            「导出对话历史」挤掉。名字挂在 ``title``/``aria-label`` 上 —— 鼠标停上去显示
            「所有交付物」。
          */}
          <button
            type="button"
            className={`chat-top-icon${deliverableTotal > 0 ? " busy" : ""}`}
            aria-label="所有交付物"
            title="所有交付物"
            onClick={onOpenChest}
          >
            <TreasureVisual state={deliverableTotal > 0 ? "ready" : "none"} size="mini" />
          </button>
          <button type="button" className="ht-btn" onClick={onExportHistory} title="导出对话历史：勾选会话，下载原始 jsonl">
            <Download size={14} />导出对话历史
          </button>
          <button type="button" className="ht-btn primary" onClick={onNewTask}><Plus size={14} />新建任务</button>
        </div>
      </div>
      <div className="ht-stat-row">
        {statCell(String(counts.working), "进行中", "todo 汇总里还有未完成项的会话数")}
        {statCell(String(counts.attention), "待处理", "有 todo 但没有任何一项在推进的会话数")}
        <button type="button" className="ht-stat ht-stat-action" onClick={onOpenNewDeliverables} title="本轮新收到、还没打开过的交付物">
          <TreasureVisual state={newDeliveryCount > 0 ? "ready" : "none"} size="compact" /><strong>{newDeliveryCount}</strong><em>新交付物</em>
        </button>
        {statCell(String(monthlyRuns), "本月执行", "本自然月内跑过 todo 的会话数（按会话去重，同一会话多次只算一次）")}
      </div>
      <div className="ht-task-toolbar">
        <div className="ht-filter-chips">
          {filters.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`ht-chip${filter === key ? " primary" : ""}`}
              aria-pressed={filter === key}
              onClick={() => onFilter(key)}
            >
              {label} {counts[key]}
            </button>
          ))}
        </div>
        <label className="ht-task-search"><Search size={13} /><input placeholder="搜索任务或交付物" value={search} onChange={(e) => onSearch(e.target.value)} /></label>
      </div>
      <div className="ht-dt-split">
        <div>
          <div className="ht-table-wrap">
            <table className="ht-table">
              <thead><tr><th>任务</th><th>状态</th><th>进度</th><th>流程</th><th>负责人</th><th>更新时间</th><th></th></tr></thead>
              <tbody>
                {filtered.length === 0 && <tr><td colSpan={7} className="ht-table-empty">没有找到匹配的任务</td></tr>}
                {filtered.map((t) => (
                  /*
                   * 单击 = 选中(右侧详情面板跟着换); **双击 = 直接打开这条对话**。
                   *
                   * 加双击是因为「打开」此前只有详情面板里那个「继续对话」一个入口 —— 想进对话
                   * 得先点行、再把视线挪到右侧面板、再点一次, 而这一行的主要用途就是进去接着聊。
                   * 保留单击选中(不是直接打开): 双击的第一下会先把它选中, 于是「先看看再决定进不进去」
                   * 这条路没有被堵掉。
                   */
                  <tr
                    key={t.id}
                    className="ht-row"
                    onClick={() => onSelect(t.id)}
                    onDoubleClick={() => onOpenChat(t.id)}
                    title="双击打开对话"
                    aria-selected={selected?.id === t.id}
                  >
                    <td>
                      <div className="ht-cell-main">
                        <strong>{t.title}</strong>
                        {t.fromIm && (
                          <span className="ht-badge-im" title="这条会话与飞书 IM 里的对话共通, 双向可见">
                            来自飞书对话
                          </span>
                        )}
                        {t.readOnly && (
                          <span className="ht-badge-ro" title="组织共享任务: 历史公开可读, 但谁都不能在里面发消息">
                            组织共享 · 只读
                          </span>
                        )}
                        <em>{t.sop}</em>
                      </div>
                    </td>
                    <td>{statusPill(t.status)}</td>
                    <td><div className="ht-cell-progress"><div className="ht-bar"><i style={{ width: `${t.progress}%` }} /></div><small>{t.progress}%</small></div></td>
                    <td><span className="ht-cell-sop"><Workflow size={12} />{t.sop}</span></td>
                    <td><span className="ht-avatars"><span className="ht-avatar navy">海</span></span></td>
                    <td className="ht-muted">{t.updated}</td>
                    <td>
                      <button
                        type="button"
                        className={`ht-row-pin${t.pinned ? " is-pinned" : ""}`}
                        aria-label={t.pinned ? "取消置顶" : "置顶"}
                        title={t.pinned ? "取消置顶" : "置顶(只影响你自己的列表)"}
                        aria-pressed={t.pinned}
                        onClick={(e) => { e.stopPropagation(); onTogglePin(t.id); }}
                      ><Pin size={14} /></button>
                      {/* IM 共用那条不许删: 它承载的是与飞书机器人的同一条对话, 删了等于
                          把机器人的上下文一起扔掉, 而用户在 IM 里还会继续用到它。
                          组织共享那条也不给删: 后端对它的写操作一律 403, 摆一个点了必失败的
                          按钮只会让人以为是坏的。 */}
                      {!t.fromIm && !t.readOnly && (
                        <button type="button" className="ht-row-delete" aria-label="删除任务" title="删除任务" onClick={(e) => { e.stopPropagation(); onDelete(t.id); }}><Trash2 size={14} /></button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <aside className="ht-detail-side ht-task-aside">
          {selected && (
            <>
              <div className="ht-card">
                <div className="ht-section-label"><span>当前任务</span><span className="ht-version">{selected.sop}</span></div>
                <h3>{selected.title}</h3>
                <p>{selected.owner} · {selected.updated}</p>
                {selected.contextWarning && (
                  <p className="ht-card-hint">
                    这条会话与飞书对话共用同一份上下文, 会越来越长。建议点上方「新建任务」开一个新会话。
                  </p>
                )}
                {selected.readOnly && (
                  <p className="ht-card-hint">
                    组织共享任务: 历史对所有人可读, 但谁都不能在里面发消息。点上方「新建任务」开一个自己的会话继续做。
                  </p>
                )}
                <div className="ht-bar" role="progressbar" aria-valuenow={selected.progress} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${selected.progress}%` }} /></div>
                <div className="ht-steps">{selected.steps.map((s, i) => <div key={i} className={`ht-step ${s.s}`}><span>{s.s === "done" ? <Check size={12} /> : i + 1}</span><em>{s.t}</em></div>)}</div>
                <div className="ht-actions">
                  <button type="button" className="ht-btn primary" onClick={() => onOpenChat(selected.id)}><MessageCircle size={13} />{selected.readOnly ? "查看历史" : "继续对话"}</button>
                  {!selected.fromIm && !selected.readOnly && (
                    <button type="button" className="ht-btn" onClick={() => onDelete(selected.id)}><Trash2 size={13} />删除</button>
                  )}
                </div>
              </div>
              <div className="ht-card">
                <div className="ht-section-label"><span>新交付物</span><em>{newDeliveryCount}</em></div>
                <p>点击统计数字或下方按钮，从右侧打开待确认的新交付物。</p>
                <button type="button" className="ht-btn soft" onClick={onOpenNewDeliverables}><TreasureVisual state={newDeliveryCount > 0 ? "ready" : "none"} size="mini" />打开新交付物</button>
                {/* 全部交付物的入口是页头那颗宝箱图标(「所有交付物」), 这里不再重复一个按钮:
                    同一件事在一屏里出现两次, 只会让人犹豫点哪个。 */}
              </div>
            </>
          )}
        </aside>
      </div>
    </>
  );
}
