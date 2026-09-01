import { useEffect, useRef } from 'react'

import { fillOtp } from '../../services/authFlow'

type Props = {
  value: string
  onChange: (next: string) => void
  /** 6 位填满时触发自动提交（原型 A2：不必点按钮）。 */
  onComplete?: (code: string) => void
  /** D1 错误态：整体转红并轻抖，不逐格标记。 */
  invalid?: boolean
  disabled?: boolean
  /** 挂载时自动聚焦第 1 格。 */
  autoFocus?: boolean
}

const SLOTS = [0, 1, 2, 3, 4, 5]

/**
 * 验证码 6 格输入。
 *
 * 用 6 个独立 input 而非一个宽输入框：原型要求逐格显示，且 `autocomplete=
 * "one-time-code"` 在 6 格形态下同样能触发系统短信填充。分填与截断的规则全在
 * `authFlow.fillOtp` 里，此处只管焦点与事件 —— 逻辑留在纯函数里才测得动。
 */
export default function HubOtpInput({
  value,
  onChange,
  onComplete,
  invalid = false,
  disabled = false,
  autoFocus = false,
}: Props) {
  const refs = useRef<(HTMLInputElement | null)[]>([])
  // 已触发过自动提交的码：避免同一个码因重渲染重复提交
  const submitted = useRef('')

  useEffect(() => {
    if (autoFocus) refs.current[0]?.focus()
  }, [autoFocus])

  useEffect(() => {
    if (value.length === 6 && submitted.current !== value) {
      submitted.current = value
      onComplete?.(value)
    }
    if (value.length < 6) submitted.current = ''
  }, [value, onComplete])

  const focusAt = (i: number) => {
    const el = refs.current[Math.min(Math.max(i, 0), 5)]
    el?.focus()
    el?.select()
  }

  const onInput = (i: number, rawValue: string) => {
    const digits = rawValue.replace(/\D/g, '')
    const prev = value[i] ?? ''
    /* `e.target.value` 是**整格的新值**，不是刚敲的那一位：格里已有字符时再敲，
     * 浏览器给的是「原有 + 新」两位。若只按位数判断意图，改写一位会被误当成
     * 粘贴整段（把 123456 冲成 "19"），同格连敲还会填出重复位 —— 用户看到的
     * 就是"同时打进两个相同的数"和"正确的码被判错"。
     *
     * 所以在这里先剥掉格内原有字符，再把「单键」与「粘贴」分开交给 fillOtp：
     * 组件知道哪一格原本是什么，纯函数不知道，这个判断只能在这里做。 */
    const typed = prev && digits.startsWith(prev) ? digits.slice(prev.length) : digits
    if (!typed) {
      // 敲的就是原字符本身（或被清空）：内容不变，不要误判成粘贴
      if (!digits) onChange(value.slice(0, i) + value.slice(i + 1))
      return
    }
    const isPaste = typed.length > 1
    const next = isPaste ? fillOtp(value, i, typed) : fillOtp(value, i, typed.slice(-1))
    onChange(next)
    // 粘贴整段后焦点跳到末位，单键输入后前进一格
    focusAt(isPaste ? next.length - 1 : i + 1)
  }

  const onKeyDown = (i: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace') {
      e.preventDefault()
      if (value[i]) {
        // 当前格有值：删本格，焦点不动
        onChange(value.slice(0, i) + value.slice(i + 1))
        return
      }
      // 当前格为空：回退上一格并删掉它（原型 A2「退格回退上一格」）
      if (i > 0) {
        onChange(value.slice(0, i - 1) + value.slice(i))
        focusAt(i - 1)
      }
      return
    }
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      focusAt(i - 1)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      focusAt(i + 1)
    }
  }

  return (
    <div className={`hub-otp${invalid ? ' bad' : ''}`} role="group" aria-label="6 位验证码">
      {SLOTS.map((i) => (
        <input
          key={i}
          ref={(el) => {
            refs.current[i] = el
          }}
          value={value[i] ?? ''}
          onChange={(e) => onInput(i, e.target.value)}
          onKeyDown={(e) => onKeyDown(i, e)}
          onFocus={(e) => e.currentTarget.select()}
          disabled={disabled}
          inputMode="numeric"
          autoComplete="one-time-code"
          aria-label={`验证码第 ${i + 1} 位`}
          aria-invalid={invalid || undefined}
          maxLength={6}
        />
      ))}
    </div>
  )
}
