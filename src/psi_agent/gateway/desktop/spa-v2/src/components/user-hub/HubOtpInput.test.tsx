/** HubOtpInput 的真实 DOM 行为测试。
 *
 * 为什么必须在真 DOM 里测：这个组件的 bug 出在 `e.target.value` 的语义上 ——
 * 6 个格子每个都是独立 input，格里已有字符时再敲一下，浏览器给的
 * `e.target.value` 是「已有字符 + 新字符」两位，而不是刚敲的那一位。
 * 纯函数层（authFlow.fillOtp）拿单个字符测永远是绿的，只有挂到真 DOM、
 * 发真 change 事件才能暴露。
 *
 * @vitest-environment jsdom
 */

import '@testing-library/jest-dom/vitest'

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'

import HubOtpInput from './HubOtpInput'

afterEach(cleanup)

/** 受控包装：贴近 HubLoginPanel 的用法（value + onChange 回写 state）。 */
function Harness({ onComplete }: { onComplete?: (c: string) => void }) {
  const [code, setCode] = useState('')
  return (
    <>
      <HubOtpInput value={code} onChange={setCode} onComplete={onComplete} />
      <output data-testid="code">{code}</output>
    </>
  )
}

const slots = () => screen.getAllByRole('textbox') as HTMLInputElement[]
const codeText = () => screen.getByTestId('code').textContent

/** 模拟浏览器真实行为：在 input 尾部插入一个字符后触发 change。
 *
 * 这正是 bug 的来源 —— 不是 fireEvent 的怪癖，而是真实键入时
 * `e.target.value` 会包含格内原有字符。
 */
function typeInto(el: HTMLInputElement, ch: string) {
  fireEvent.change(el, { target: { value: (el.value ?? '') + ch } })
}

describe('逐格键入', () => {
  it('空格子依次键入 6 位，结果就是所键顺序', () => {
    render(<Harness />)
    const s = slots()
    for (const [i, ch] of [...'123456'].entries()) typeInto(s[i], ch)
    expect(codeText()).toBe('123456')
  })

  it('回归：格内已有值时改写该格，不能把整串冲成两位', () => {
    render(<Harness />)
    const s = slots()
    for (const [i, ch] of [...'123456'].entries()) typeInto(s[i], ch)
    expect(codeText()).toBe('123456')

    // 回到第 1 格改成 9：浏览器给出 "19"（原有"1" + 新键"9"）
    typeInto(s[0], '9')
    expect(codeText()).toBe('923456')
  })

  it('回归：同一格连敲两下同一数字，不产生重复位', () => {
    render(<Harness />)
    const s = slots()
    typeInto(s[0], '1')
    expect(codeText()).toBe('1')
    // 焦点已前进，但用户又在第 0 格敲了一次 1
    typeInto(s[0], '1')
    expect(codeText()).toBe('1')
  })
})

describe('粘贴', () => {
  it('整段 6 位从头铺满', () => {
    render(<Harness />)
    fireEvent.change(slots()[0], { target: { value: '654321' } })
    expect(codeText()).toBe('654321')
  })

  it('超长截断到 6 位', () => {
    render(<Harness />)
    fireEvent.change(slots()[0], { target: { value: '1234567890' } })
    expect(codeText()).toBe('123456')
  })

  it('非数字被剔除', () => {
    render(<Harness />)
    fireEvent.change(slots()[0], { target: { value: 'a1b2c3' } })
    expect(codeText()).toBe('123')
  })
})

describe('退格', () => {
  it('当前格有值：删本格', () => {
    render(<Harness />)
    const s = slots()
    for (const [i, ch] of [...'123'].entries()) typeInto(s[i], ch)
    fireEvent.keyDown(s[2], { key: 'Backspace' })
    expect(codeText()).toBe('12')
  })

  it('当前格为空：回退并删上一格', () => {
    render(<Harness />)
    const s = slots()
    for (const [i, ch] of [...'12'].entries()) typeInto(s[i], ch)
    fireEvent.keyDown(s[2], { key: 'Backspace' })
    expect(codeText()).toBe('1')
  })
})

describe('自动提交', () => {
  it('填满 6 位触发一次，且提交的是正确的码', () => {
    const onComplete = vi.fn()
    render(<Harness onComplete={onComplete} />)
    const s = slots()
    for (const [i, ch] of [...'112233'].entries()) typeInto(s[i], ch)
    expect(codeText()).toBe('112233')
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith('112233')
  })

  it('改写其中一位后重新填满，提交改写后的码', () => {
    const onComplete = vi.fn()
    render(<Harness onComplete={onComplete} />)
    const s = slots()
    for (const [i, ch] of [...'123456'].entries()) typeInto(s[i], ch)
    onComplete.mockClear()
    fireEvent.keyDown(s[5], { key: 'Backspace' })   // 退一位，回到 5 位
    typeInto(s[5], '9')                              // 再填满
    expect(codeText()).toBe('123459')
    expect(onComplete).toHaveBeenLastCalledWith('123459')
  })
})
