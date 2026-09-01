import { describe, expect, it } from 'vitest'

import {
  AUTH_ERROR_TEXT,
  attemptsExhausted,
  attemptsText,
  codeTtlMinutes,
  cooldownFrom,
  errorCodeOf,
  failScreenFor,
  fillOtp,
  groupPhone,
  humanizeAuthError,
  isDailyCap,
  isNewUser,
  isOtpComplete,
  isTempTokenExpired,
  isValidEmail,
  isValidPhone,
  maskAccount,
  maskPhone,
  needsComplete,
  normalizeCode,
  remainingOf,
  retryAfterOf,
  screenOf,
  validateAccount,
  type ScreenId,
  type ScreenState,
} from './authFlow'

describe('手机号校验与服务端同规则', () => {
  it('接受大陆号段', () => {
    expect(isValidPhone('13800000000')).toBe(true)
    expect(isValidPhone('19912345678')).toBe(true)
    expect(isValidPhone(' 13800000000 ')).toBe(true)
  })

  it('拒绝非大陆号段与长度不符', () => {
    expect(isValidPhone('12800000000')).toBe(false) // 第二位 2 不在 3-9
    expect(isValidPhone('1380000000')).toBe(false) // 10 位
    expect(isValidPhone('138000000000')).toBe(false) // 12 位
    expect(isValidPhone('')).toBe(false)
    expect(isValidPhone('abcdefghijk')).toBe(false)
  })

  it('不自行去 +86 前缀：归一化是服务端的职责，前端只做格式提示', () => {
    // 若前端偷偷去前缀，用户会以为格式没问题；实际请求体仍带 +86，
    // 反而掩盖问题。这里明确断言前端不做这件事。
    expect(isValidPhone('+8613800000000')).toBe(false)
  })
})

describe('邮箱校验', () => {
  it('接受常见写法', () => {
    expect(isValidEmail('u@example.com')).toBe(true)
    expect(isValidEmail('u.s+tag@ex.co')).toBe(true)
  })

  it('拒绝缺域名/多个 @/空格/点号位置异常', () => {
    for (const bad of ['', 'u@', '@x.com', 'u@x', 'a@b@c.com', 'u x@a.com', 'u@.com', 'u@x.']) {
      expect(isValidEmail(bad)).toBe(false)
    }
  })
})

describe('发码前本地校验', () => {
  it('手机号通道给出手机号专属提示', () => {
    expect(validateAccount('phone', '123')).toContain('手机号')
    expect(validateAccount('phone', '13800000000')).toBe('')
  })

  it('邮箱通道给出邮箱专属提示', () => {
    expect(validateAccount('email', 'nope')).toContain('邮箱')
    expect(validateAccount('email', 'u@example.com')).toBe('')
  })
})

describe('验证码输入归一化', () => {
  it('剔除非数字并截到 6 位', () => {
    expect(normalizeCode('12 34 56')).toBe('123456')
    expect(normalizeCode('1234567890')).toBe('123456')
    expect(normalizeCode('abc123')).toBe('123')
    expect(normalizeCode('')).toBe('')
  })
})

describe('错误码文案', () => {
  it('已知码译成中文', () => {
    expect(humanizeAuthError(new Error('code_invalid'))).toBe('验证码不正确或已过期，请重新获取')
    expect(humanizeAuthError(new Error('rate_limited'))).toBe('操作过于频繁，请稍后再试')
  })

  it('未知码原样透出，不吞掉线索', () => {
    expect(humanizeAuthError(new Error('some_new_code'))).toBe('some_new_code')
  })

  it('空错误给出兜底文案', () => {
    expect(humanizeAuthError(new Error(''))).toBe('操作失败，请重试')
    expect(humanizeAuthError(null)).toBe('操作失败，请重试')
  })

  // 这十个码取自云端 core/errors.py 的**实际** code 常量 + FastAPI 校验失败的
  // invalid_request, 不是取自契约文档 —— 文档里写过 invalid_code / code_expired /
  // invitation_* 这些服务端从未发出的码, 照文档写测试只会测出一份自洽的幻觉。
  it('云端实际发出的每个错误码都有文案（漏一个用户就会看到英文码）', () => {
    for (const code of [
      'code_invalid',
      'rate_limited',
      'unauthorized',
      'forbidden',
      'not_found',
      'conflict',
      'identity_taken',
      'last_identity',
      'invalid_input',
      'provider_failure',
      'invalid_request',
    ]) {
      expect(AUTH_ERROR_TEXT[code], `缺少 ${code} 的文案`).toBeTruthy()
    }
  })
})

describe('倒计时以服务端 retryAfter 为准', () => {
  it('用服务端给的秒数', () => {
    expect(cooldownFrom(60)).toBe(60)
    expect(cooldownFrom(15)).toBe(15)
  })

  it('缺失或非法时回落 60 秒，不给 0（否则按钮立刻可再点、必被限频拒）', () => {
    expect(cooldownFrom(undefined)).toBe(60)
    expect(cooldownFrom(0)).toBe(60)
    expect(cooldownFrom(-5)).toBe(60)
    expect(cooldownFrom('60')).toBe(60)
  })
})

describe('两段式注册判断', () => {
  it('registrationRequired 时需要走 complete', () => {
    expect(needsComplete({ registrationRequired: true })).toBe(true)
    expect(isNewUser({ registrationRequired: true, isNewUser: true })).toBe(true)
  })

  it('拿到 token 即老用户，不该再调 complete', () => {
    expect(needsComplete({ token: 'tok-1' })).toBe(false)
    expect(isNewUser({ token: 'tok-1' })).toBe(false)
  })

  it('token 与 registrationRequired 同时存在时按老用户处理（不重复建号）', () => {
    expect(needsComplete({ token: 'tok-1', registrationRequired: true })).toBe(false)
    expect(isNewUser({ token: 'tok-1', registrationRequired: true, isNewUser: true })).toBe(false)
  })

  /* 回归：`tempToken` 曾是新用户信号，Gateway 改成扣住凭证后它不再下发，判断却
   * 还在看它 —— 新用户于是直落"未登录"被弹回输入页。这条钉住"旧字段不再有效"。 */
  it('残留的 tempToken 字段不再被当作新用户信号', () => {
    expect(needsComplete({ tempToken: 'tt-1' } as { token?: string })).toBe(false)
  })
})

describe('手机号显示：3-4-4 分组与中间四位打码', () => {
  it('按 3-4-4 分组（原型 A1）', () => {
    expect(groupPhone('13800138000')).toBe('138 0013 8000')
    expect(groupPhone('138')).toBe('138')
    expect(groupPhone('1380')).toBe('138 0')
    expect(groupPhone('')).toBe('')
  })

  it('超出 11 位截断，非数字剔除', () => {
    expect(groupPhone('138001380009999')).toBe('138 0013 8000')
    expect(groupPhone('138-0013-8000')).toBe('138 0013 8000')
  })

  it('中间四位打码（原型 A2）', () => {
    expect(maskPhone('13800138000')).toBe('+86 138****8000')
  })

  it('位数不足不打码：宁可原样显示，也不给一个看起来像完整号的假串', () => {
    expect(maskPhone('1380013')).toBe('1380013')
  })

  it('邮箱完整显示不打码 —— 用户要核对域名是否填错（原型 B2）', () => {
    expect(maskAccount('email', ' User@Example.com ')).toBe('User@Example.com')
    expect(maskAccount('phone', '13800138000')).toBe('+86 138****8000')
  })
})

describe('验证码有效期文案随通道不同', () => {
  it('短信 5 分钟由 PNVS 托管，邮箱 10 分钟我们自管', () => {
    expect(codeTtlMinutes('phone')).toBe(5)
    expect(codeTtlMinutes('email')).toBe(10)
  })
})

describe('OTP 6 格分填', () => {
  it('整段粘贴从头铺满，超出截断', () => {
    expect(fillOtp('', 0, '123456')).toBe('123456')
    expect(fillOtp('', 3, '123456')).toBe('123456') // 粘到第 4 格也从头铺
    expect(fillOtp('', 0, '1234567890')).toBe('123456')
  })

  it('单键输入只改当前格', () => {
    expect(fillOtp('123456', 0, '9')).toBe('923456')
    expect(fillOtp('123456', 5, '9')).toBe('123459')
  })

  it('在末尾之后的空格里打字等价于追加', () => {
    expect(fillOtp('12', 5, '9')).toBe('129')
    expect(fillOtp('', 4, '7')).toBe('7')
  })

  it('非数字输入被忽略，不清空已填', () => {
    expect(fillOtp('123', 3, 'a')).toBe('123')
    expect(fillOtp('123', 3, '')).toBe('123')
  })

  it('填满 6 位才算完整', () => {
    expect(isOtpComplete('123456')).toBe(true)
    expect(isOtpComplete('12345')).toBe(false)
    expect(isOtpComplete('12345a')).toBe(false)
  })
})

describe('错误码归屏', () => {
  it('限频类归 D2（带倒计时的黄条）', () => {
    expect(failScreenFor('rate_limited')).toBe('D2')
    expect(failScreenFor('FREQUENCY_FAIL')).toBe('D2')
    expect(failScreenFor('BUSINESS_LIMIT_CONTROL')).toBe('D2')
  })

  it('断网与登录态失效归 D3', () => {
    expect(failScreenFor('upstream_unreachable')).toBe('D3')
    expect(failScreenFor('unauthorized')).toBe('D3')
  })

  it('码错类归 D1，就地提示不跳屏（跳屏会丢用户已填内容）', () => {
    expect(failScreenFor('code_invalid')).toBe('D1')
    expect(failScreenFor('invalid_input')).toBe('D1')
    expect(failScreenFor('some_unknown_code')).toBe('D1')
  })

  it('当日上限单列：处置是「改用邮箱」而非「等一会儿」', () => {
    expect(isDailyCap('BUSINESS_LIMIT_CONTROL')).toBe(true)
    expect(isDailyCap('FREQUENCY_FAIL')).toBe(false)
    expect(isDailyCap('rate_limited')).toBe(false)
  })

  it('两个阿里云码都有中文文案，否则用户看到英文码', () => {
    expect(AUTH_ERROR_TEXT.FREQUENCY_FAIL).toBeTruthy()
    expect(AUTH_ERROR_TEXT.BUSINESS_LIMIT_CONTROL).toBeTruthy()
    // 两句必须不同：合并成一句「过于频繁」会让当日上限的用户一直干等
    expect(AUTH_ERROR_TEXT.FREQUENCY_FAIL).not.toBe(AUTH_ERROR_TEXT.BUSINESS_LIMIT_CONTROL)
  })

  it('tempToken 过期可识别，用于退回 A1 而非停在 A3 死等', () => {
    expect(isTempTokenExpired('temp_token_invalid')).toBe(true)
    expect(isTempTokenExpired('code_invalid')).toBe(false)
  })
})

describe('从错误对象取限频字段', () => {
  it('AuthApiError 形状的 retryAfter / remaining 能取到', () => {
    const e = Object.assign(new Error('rate_limited'), { retryAfter: 48, remaining: 3 })
    expect(retryAfterOf(e)).toBe(48)
    expect(remainingOf(e)).toBe(3)
    expect(errorCodeOf(e)).toBe('rate_limited')
  })

  it('普通 Error 取不到，回落 undefined 而非 0', () => {
    // 回落成 0 会让倒计时立刻可点，按钮一亮就再撞一次限频
    expect(retryAfterOf(new Error('x'))).toBeUndefined()
    expect(remainingOf(new Error('x'))).toBeUndefined()
    expect(retryAfterOf(null)).toBeUndefined()
    expect(remainingOf(undefined)).toBeUndefined()
  })

  it('remaining 为 0 要能取到（0 是有效值，不能被当成缺失）', () => {
    const e = Object.assign(new Error('code_invalid'), { remaining: 0 })
    expect(remainingOf(e)).toBe(0)
  })

  it('非正 retryAfter 视为缺失，交给 cooldownFrom 回落 60s', () => {
    expect(retryAfterOf(Object.assign(new Error('x'), { retryAfter: 0 }))).toBeUndefined()
    expect(retryAfterOf(Object.assign(new Error('x'), { retryAfter: -3 }))).toBeUndefined()
  })
})

describe('校验剩余次数文案', () => {
  it('给了次数就明示，让用户自己决定重发还是重试', () => {
    expect(attemptsText(3)).toBe('验证码不正确，还可尝试 3 次')
    expect(attemptsText(1)).toBe('验证码不正确，还可尝试 1 次')
  })

  it('次数耗尽转为要求重新获取', () => {
    expect(attemptsText(0)).toBe('尝试次数过多，请重新获取验证码')
    expect(attemptsExhausted(0)).toBe(true)
  })

  it('服务端没给次数就只说不正确，不猜数字', () => {
    // 猜错比不说更糟：用户以为还有 3 次，其实下一次就被锁
    expect(attemptsText(undefined)).toBe('验证码不正确')
    expect(attemptsText('3')).toBe('验证码不正确')
    expect(attemptsExhausted(undefined)).toBe(false)
  })
})

describe('原型 12 屏可达性', () => {
  // 每一屏对应一组状态。这张表就是「屏是否点得到」的可执行版本 ——
  // 少一屏、或两个状态撞到同一屏，都会在这里红。
  const cases: [ScreenId, ScreenState][] = [
    ['A1', { step: 'input', channel: 'phone' }],
    ['A1b', { step: 'input', channel: 'phone', sending: true }],
    ['A2', { step: 'code', channel: 'phone' }],
    ['A3', { step: 'profile', channel: 'phone' }],
    ['B1', { step: 'input', channel: 'email' }],
    ['B2', { step: 'code', channel: 'email' }],
    ['C1', { step: 'account', channel: 'phone' }],
    ['C2', { step: 'devices', channel: 'phone' }],
    ['D1', { step: 'code', channel: 'phone', codeError: true }],
    ['D2', { step: 'input', channel: 'phone', rateLimited: true }],
    ['D3', { step: 'offline', channel: 'phone' }],
    ['D4', { step: 'finishing', channel: 'phone' }],
  ]

  it.each(cases)('%s 可达', (want, state) => {
    expect(screenOf(state)).toBe(want)
  })

  it('12 屏全部覆盖，无重复', () => {
    const ids = cases.map(([id]) => id)
    expect(new Set(ids).size).toBe(12)
  })

  it('失败态优先于正常态：限频要盖住 A1，码错要盖住 A2', () => {
    // 否则用户看到的是一个「一切正常」的界面配一行小字错误，与原型不符
    expect(screenOf({ step: 'input', channel: 'phone', rateLimited: true })).toBe('D2')
    expect(screenOf({ step: 'code', channel: 'email', codeError: true })).toBe('D1')
  })

  it('D1 不分通道：手机与邮箱的码错都落同一屏', () => {
    expect(screenOf({ step: 'code', channel: 'phone', codeError: true })).toBe('D1')
    expect(screenOf({ step: 'code', channel: 'email', codeError: true })).toBe('D1')
  })
})
