function abortError(signal) {
  if (isAbortError(signal?.reason)) return signal.reason
  const error = new Error('Operation aborted')
  error.name = 'AbortError'
  return error
}

export function isAbortError(error) {
  return !!error && typeof error === 'object' && error.name === 'AbortError'
}

export function throwIfAborted(signal) {
  if (signal?.aborted) throw abortError(signal)
}

/** Read a browser File as base64 while forwarding AbortSignal to FileReader. */
export function readFileAsBase64(file, signal, createReader = () => new FileReader()) {
  try {
    throwIfAborted(signal)
  } catch (error) {
    return Promise.reject(error)
  }

  return new Promise((resolve, reject) => {
    const reader = createReader()
    let settled = false

    const cleanup = () => signal?.removeEventListener('abort', onSignalAbort)
    const finish = (callback, value) => {
      if (settled) return
      settled = true
      cleanup()
      callback(value)
    }
    const failAsAborted = () => finish(reject, abortError(signal))
    const onSignalAbort = () => {
      try {
        reader.abort()
      } catch {}
      failAsAborted()
    }

    reader.onload = () => {
      const result = reader.result
      if (typeof result !== 'string' || !result.includes(',')) {
        finish(reject, new Error('FileReader returned an invalid data URL'))
        return
      }
      finish(resolve, result.slice(result.indexOf(',') + 1))
    }
    reader.onerror = () => finish(reject, reader.error || new Error('FileReader failed'))
    reader.onabort = failAsAborted
    signal?.addEventListener('abort', onSignalAbort, { once: true })

    try {
      reader.readAsDataURL(file)
    } catch (error) {
      finish(reject, error)
    }
  })
}
