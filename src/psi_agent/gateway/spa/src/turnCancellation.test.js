import { describe, expect, it } from 'vitest'

import {
  isAbortError,
  readFileAsBase64,
  throwIfAborted,
} from './turnCancellation.js'

class FakeFileReader {
  constructor() {
    this.aborted = false
    this.result = null
    this.onload = null
    this.onerror = null
    this.onabort = null
  }

  readAsDataURL() {}

  abort() {
    this.aborted = true
    this.onabort?.()
  }
}

describe('turn cancellation', () => {
  it('fails before starting work when the signal is already aborted', async () => {
    const controller = new AbortController()
    controller.abort()
    let readerCreated = false

    await expect(readFileAsBase64({}, controller.signal, () => {
      readerCreated = true
      return new FakeFileReader()
    })).rejects.toMatchObject({ name: 'AbortError' })
    expect(readerCreated).toBe(false)
    expect(() => throwIfAborted(controller.signal)).toThrowError(expect.objectContaining({
      name: 'AbortError',
    }))
  })

  it('aborts an in-flight FileReader and rejects with AbortError', async () => {
    const controller = new AbortController()
    const reader = new FakeFileReader()
    const pending = readFileAsBase64({}, controller.signal, () => reader)

    controller.abort()

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(reader.aborted).toBe(true)
  })

  it('returns the base64 payload and removes the abort listener after success', async () => {
    const controller = new AbortController()
    const reader = new FakeFileReader()
    reader.readAsDataURL = () => {
      reader.result = 'data:text/plain;base64,SGVsbG8='
      reader.onload()
    }

    await expect(readFileAsBase64({}, controller.signal, () => reader)).resolves.toBe('SGVsbG8=')
    controller.abort()
    expect(reader.aborted).toBe(false)
  })

  it('recognizes only AbortError-shaped failures as cancellation', () => {
    const abort = new Error('stopped')
    abort.name = 'AbortError'
    expect(isAbortError(abort)).toBe(true)
    expect(isAbortError(new Error('network failed'))).toBe(false)
    expect(isAbortError(null)).toBe(false)
  })
})
