import type { KeyboardEvent } from 'react'

/**
 * Composer keyboard:
 * - Enter → submit (send)
 * - Ctrl/Cmd+Enter → insert newline
 * - Shift+Enter → native newline (do not preventDefault)
 */
export function onComposerEnterKey(
  event: KeyboardEvent<HTMLTextAreaElement>,
  value: string,
  insertNewline: (next: string, cursor: number) => void,
): void {
  if (event.key !== 'Enter' || event.nativeEvent.isComposing) return

  if ((event.ctrlKey || event.metaKey) && !event.altKey) {
    event.preventDefault()
    const el = event.currentTarget
    const start = el.selectionStart ?? value.length
    const end = el.selectionEnd ?? value.length
    insertNewline(`${value.slice(0, start)}\n${value.slice(end)}`, start + 1)
    return
  }

  if (event.shiftKey || event.altKey) return

  event.preventDefault()
  event.currentTarget.form?.requestSubmit()
}
