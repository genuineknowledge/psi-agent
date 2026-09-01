/**
 * spa-v2 interaction surface flags.
 *
 * **赶工临时 / 刻意为之**：总览卡、模板库的组件 / fixture / 状态保留，仅从主导航与
 * 卡片栈暂时摘掉，方便一键恢复。改回 ``true`` 即还原旧交互。
 */
export const SHOW_OVERVIEW_AND_TEMPLATES = false

/** Map a task list index → card-stack index (accounts for optional overview at 0). */
export function cardIndexForTask(taskIndex: number): number {
  return SHOW_OVERVIEW_AND_TEMPLATES ? taskIndex + 1 : taskIndex
}

/** Task at a card-stack index, or ``null`` when the slot is the overview card. */
export function taskAtCardIndex<T>(tasks: readonly T[], cardIndex: number): T | null {
  if (SHOW_OVERVIEW_AND_TEMPLATES) {
    if (cardIndex <= 0) return null
    return tasks[cardIndex - 1] ?? null
  }
  return tasks[cardIndex] ?? null
}
