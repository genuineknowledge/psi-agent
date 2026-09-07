/** One follow-up waiting until the current card turn finishes (Cursor-style). */
export type QueuedSend = {
  text: string
  files: File[]
  /** User-visible label in the composer queue chip. */
  display: string
}

export function buildQueuedSend(
  text: string,
  files: File[],
  uploadedPrefix: string,
): QueuedSend | null {
  const clean = text.trim()
  if (!clean && !files.length) return null
  const display =
    clean || `${uploadedPrefix}${files.map((file) => file.name).join('、')}`
  return { text: clean, files: [...files], display }
}

/** Replace any existing queue for this card (one pending follow-up at a time). */
export function setQueuedForCard(
  current: Record<string, QueuedSend | null | undefined>,
  cardId: string,
  next: QueuedSend | null,
): Record<string, QueuedSend | null> {
  return { ...current, [cardId]: next }
}
