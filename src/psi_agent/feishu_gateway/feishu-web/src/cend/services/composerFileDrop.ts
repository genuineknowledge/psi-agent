import { useCallback, useRef, useState, type DragEvent } from 'react'
import { filesFromClipboard } from './clipboardFiles'

/** True when the drag payload can yield files (Explorer / other windows / OS). */
export function dataTransferHasFiles(data: DataTransfer | null | undefined): boolean {
  if (!data) return false
  if (data.files && data.files.length > 0) return true
  const types = data.types ? Array.from(data.types) : []
  return types.includes('Files')
}

type ComposerFileDropOptions = {
  enabled?: boolean
  onFiles: (files: File[]) => void
}

/**
 * Drop zone for composer attachments — same ``File[]`` path as paste / paperclip.
 * Uses enter/leave depth so child hover does not flicker the highlight off.
 */
export function useComposerFileDrop({
  enabled = true,
  onFiles,
}: ComposerFileDropOptions): {
  isFileDragOver: boolean
  dropProps: {
    onDragEnter: (event: DragEvent<HTMLElement>) => void
    onDragOver: (event: DragEvent<HTMLElement>) => void
    onDragLeave: (event: DragEvent<HTMLElement>) => void
    onDrop: (event: DragEvent<HTMLElement>) => void
  }
} {
  const [isFileDragOver, setIsFileDragOver] = useState(false)
  const depthRef = useRef(0)

  const reset = useCallback(() => {
    depthRef.current = 0
    setIsFileDragOver(false)
  }, [])

  const onDragEnter = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!enabled || !dataTransferHasFiles(event.dataTransfer)) return
      event.preventDefault()
      event.stopPropagation()
      depthRef.current += 1
      setIsFileDragOver(true)
    },
    [enabled],
  )

  const onDragOver = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!enabled || !dataTransferHasFiles(event.dataTransfer)) return
      event.preventDefault()
      event.stopPropagation()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
    },
    [enabled],
  )

  const onDragLeave = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!enabled) return
      event.preventDefault()
      event.stopPropagation()
      depthRef.current = Math.max(0, depthRef.current - 1)
      if (depthRef.current === 0) setIsFileDragOver(false)
    },
    [enabled],
  )

  const onDrop = useCallback(
    (event: DragEvent<HTMLElement>) => {
      if (!enabled) return
      event.preventDefault()
      event.stopPropagation()
      reset()
      const files = filesFromClipboard(event.dataTransfer)
      if (files.length) onFiles(files)
    },
    [enabled, onFiles, reset],
  )

  return {
    isFileDragOver,
    dropProps: { onDragEnter, onDragOver, onDragLeave, onDrop },
  }
}
