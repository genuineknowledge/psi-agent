/** 登录面板的渲染冒烟测：真挂载组件，走真实交互，断言屏上文字。
 *
 * 与 authFlow.test.ts 的分工：那边测纯逻辑（分组、打码、归屏），这边测
 * 「屏是否真的渲染出来、按钮是否真的能点到下一屏」。纯逻辑全绿但组件把某屏
 * 接错分支时，只有这一层会红。
 *
 * 后端用 authMock（URL 带 ?authMock=1 时接管 /auth/*），所以不需要起服务。
 *
 * @vitest-environment jsdom
 */

import '@testing-library/jest-dom/vitest'

import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MOCK_CODE, SCENARIOS, reset as resetFake, seedLoggedIn } from './__fixtures__/fakeAuthBackend'

/* 用模块替身把 8 个认证接口换成假后端。
 *
 * 关键在于替身只存在于测试进程：生产代码里没有任何文件 import 这个 fixture，
 * 打包器收不到它，固定验证码不会进入产物。（早先的写法是让 api.ts 静态 import
 * 假后端、靠 `?authMock=1` 切换，结果固定码确实被打进了 dist。） */
vi.mock('../../services/api', async () => {
  const real = await vi.importActual<typeof import('../../services/api')>('../../services/api')
  const fake = await import('./__fixtures__/fakeAuthBackend')
  return {
    ...real,
    getAuthStatus: fake.status,
    sendAuthCode: fake.sendCode,
    verifyAuthCode: fake.verify,
    completeAuth: fake.complete,
    getAuthMe: fake.me,
    authLogout: fake.logout,
    listAuthDevices: fake.listDevices,
    revokeAuthDevice: fake.revokeDevice,
    bindAuthIdentity: fake.bind,
  }
})

// 必须在 vi.mock 之后 import：组件要拿到被替换过的 api 模块
const { default: HubLoginPanel } = await import('./HubLoginPanel')

/** 清掉上一用例的登录态（假后端的状态是模块级的）。 */
beforeEach(() => {
  resetFake()
})

afterEach(cleanup)

const openPanel = () => render(<HubLoginPanel show onClose={() => {}} />)

/** 等 A1 出现（首次挂载会先探 /auth/status，有 180ms 延迟）。 */
const waitForA1 = () => screen.findByText('欢迎使用 HaiTun Agent')

const typePhone = (v: string) => {
  fireEvent.change(screen.getByLabelText('手机号'), { target: { value: v } })
}

/** 把 6 位码整段粘进第 1 格。 */
const fillCode = (code: string) => {
  fireEvent.change(screen.getByLabelText('验证码第 1 位'), { target: { value: code } })
}

describe('屏 A1：输入手机号', () => {
  it('渲染品牌头与双 Tab，登录屏上没有协议文字', async () => {
    openPanel()
    await waitForA1()
    expect(screen.getByRole('tab', { name: '手机号' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: '邮箱' })).toHaveAttribute('aria-selected', 'false')
    // 协议先是必勾复选框, 后改成一行被动告知, 现按团队决定整句去掉。
    // 勾选控件与告知文字都不该再有。
    expect(screen.queryByLabelText('同意协议')).toBeNull()
    expect(screen.queryByText(/登录即表示同意/)).toBeNull()
    expect(screen.queryByRole('link', { name: '《软件许可及服务协议》' })).toBeNull()
    expect(screen.queryByRole('link', { name: '《隐私保护政策》' })).toBeNull()
  })

  it('号码格式非法时主按钮禁用，合法后亮起', async () => {
    openPanel()
    await waitForA1()
    const btn = screen.getByRole('button', { name: '获取验证码' })
    typePhone('138')
    expect(btn).toBeDisabled()
    typePhone('13800138000')
    expect(btn).not.toBeDisabled()
  })

  it('号码合法即可直接发码，没有协议勾选这道门', async () => {
    openPanel()
    await waitForA1()
    typePhone(SCENARIOS.existing)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    // 勾选删掉后不该再有任何前置条件把用户拦在 A1
    expect(await screen.findByLabelText('验证码第 1 位')).toBeTruthy()
  })
})

describe('屏 B1：Tab 切到邮箱', () => {
  it('切 Tab 后换成邮箱输入框，文案随之改', async () => {
    openPanel()
    await waitForA1()
    fireEvent.click(screen.getByRole('tab', { name: '邮箱' }))
    expect(screen.getByLabelText('邮箱')).toBeTruthy()
    expect(screen.queryByLabelText('手机号')).toBeNull()
    expect(screen.getByText(/验证邮箱即可登录/)).toBeTruthy()
  })

  it('两侧输入互不清空（原型 B1 硬要求）', async () => {
    openPanel()
    await waitForA1()
    typePhone('13800138000')
    fireEvent.click(screen.getByRole('tab', { name: '邮箱' }))
    fireEvent.change(screen.getByLabelText('邮箱'), { target: { value: 'a@b.com' } })
    fireEvent.click(screen.getByRole('tab', { name: '手机号' }))
    // 回到手机号那条，原先填的号还在
    expect(screen.getByLabelText('手机号')).toHaveValue('138 0013 8000')
    fireEvent.click(screen.getByRole('tab', { name: '邮箱' }))
    expect(screen.getByLabelText('邮箱')).toHaveValue('a@b.com')
  })
})

describe('屏 A2：验证码', () => {
  it('发码后进 A2，号码中间四位打码，有效期 5 分钟', async () => {
    openPanel()
    await waitForA1()
    typePhone(SCENARIOS.existing)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    expect(await screen.findByLabelText('验证码第 1 位')).toBeTruthy()
    expect(screen.getByText('+86 138****8000')).toBeTruthy()
    expect(screen.getByText(/请在 5 分钟内完成验证/)).toBeTruthy()
  })

  it('整段粘贴自动分填 6 格', async () => {
    openPanel()
    await waitForA1()
    typePhone(SCENARIOS.existing)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    await screen.findByLabelText('验证码第 1 位')
    fillCode('987654')
    for (let i = 0; i < 6; i += 1) {
      expect(screen.getByLabelText(`验证码第 ${i + 1} 位`)).toHaveValue('987654'[i])
    }
  })

  it('返回箭头在标题栏，回 A1 且保留已填号码', async () => {
    openPanel()
    await waitForA1()
    typePhone(SCENARIOS.existing)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    await screen.findByLabelText('验证码第 1 位')
    fireEvent.click(screen.getByLabelText('返回'))
    await waitForA1()
    expect(screen.getByLabelText('手机号')).toHaveValue('138 0013 8000')
  })
})

describe('屏 B2：邮箱验证码', () => {
  it('邮箱完整显示不打码，有效期 10 分钟，垃圾邮件提示不可点', async () => {
    openPanel()
    await waitForA1()
    fireEvent.click(screen.getByRole('tab', { name: '邮箱' }))
    fireEvent.change(screen.getByLabelText('邮箱'), {
      target: { value: SCENARIOS.existingEmail },
    })
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    await screen.findByLabelText('验证码第 1 位')
    expect(screen.getByText(SCENARIOS.existingEmail)).toBeTruthy()
    expect(screen.getByText(/请在 10 分钟内完成验证/)).toBeTruthy()
    // 纯文案：不能是 button
    const hint = screen.getByText('没收到？请检查垃圾邮件文件夹')
    expect(hint.tagName).not.toBe('BUTTON')
  })
})

describe('屏 A3 → D4：新用户建号后关窗回工作台', () => {
  it('新号验证通过进 A3，提交后关窗并 toast', async () => {
    const onClose = vi.fn()
    const onToast = vi.fn()
    render(<HubLoginPanel show onClose={onClose} onToast={onToast} />)
    await waitForA1()
    typePhone('13900001111') // 非 SCENARIOS.existing，走新用户
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    await screen.findByLabelText('验证码第 1 位')
    fillCode(MOCK_CODE) // 填满即自动提交
    // A3
    expect(await screen.findByText(/为您创建新账号/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('昵称'), { target: { value: '测试用户' } })
    fireEvent.click(screen.getByRole('button', { name: '开始使用' }))
    // D4：建号收尾期间不可中断
    expect(await screen.findByText('正在为您准备账号…')).toBeTruthy()
    expect(screen.queryByLabelText('关闭')).toBeNull()
    /* 建号成功 = 关窗 + 一句 toast，**不落 C1**（原型 D4：不插一屏「登录成功」）。
       原先这里断言的是落到 C1 —— 那是在断言一个 bug：refresh() 会先把 C1 渲染出来
       再去拉 me 与设备列表，用户看到的是个只有窗框和标题的空账户面板闪一秒多。
       timeout 4s：串了 complete(700ms) + status(180ms) 两个 mock 延迟。 */
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 4000 })
    expect(onToast).toHaveBeenCalledWith('账号已创建')
    expect(screen.queryByText(/退出登录不会删除任何本地数据/)).toBeNull()
  })
})

describe('屏 C1 → C2：设备管理', () => {
  /* 已登录态下开窗 —— 这是 C1 的**唯一**真实入口（侧栏点进来看账户）。
   *
   * 原先这里是「在面板里走一遍登录」，那是错的：登录成功按原型 D4 关窗回工作台，
   * 不进 C1。它当时能过，是因为本文件的 harness 把 show 钉死为真、onClose 是空
   * 函数，面板关不掉，于是 C1 得以渲染 —— 测的正是那个「登录后闪一下空账户面板」
   * 的 bug。修掉 bug 后这几条自然红，换成从已登录态起步。 */
  const openAsLoggedIn = async () => {
    seedLoggedIn()
    openPanel()
    await screen.findByText('已登录')
  }

  it('已登录时开窗直接落在 C1', async () => {
    await openAsLoggedIn()
    expect(screen.getByText('海豚用户 8000')).toBeTruthy()
    expect(screen.getByText(/退出登录不会删除任何本地数据/)).toBeTruthy()
  })

  /* 负责人实测报的体感问题：验证码通过后「短暂显示账户与设备管理组件，但没显示完整，
     只有窗框和标题，一秒多后自己关闭」。根因是 refresh() 先 setStage('done') 把 C1
     渲染出来，再去拉 me 与设备列表，等它们回来才关窗 —— 那两个请求的往返就是那一秒多。
     现在关窗路径只探登录态，压根不进 C1。 */
  it('老用户登录成功直接关窗，中途不渲染账户面板', async () => {
    const onClose = vi.fn()
    const onToast = vi.fn()
    render(<HubLoginPanel show onClose={onClose} onToast={onToast} />)
    await waitForA1()
    typePhone(SCENARIOS.existing)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    await screen.findByLabelText('验证码第 1 位')
    fillCode(MOCK_CODE)

    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 4000 })
    expect(onToast).toHaveBeenCalledWith('已登录')
    // C1 的两处标志物一个都不该出现过
    expect(screen.queryByText(/退出登录不会删除任何本地数据/)).toBeNull()
    expect(screen.queryByRole('button', { name: /管理登录设备/ })).toBeNull()
  })

  it('进 C2 列出 3 台设备，本机行无移除按钮', async () => {
    await openAsLoggedIn()
    fireEvent.click(screen.getByRole('button', { name: /管理登录设备/ }))
    expect(await screen.findByText(/移除设备后/)).toBeTruthy()
    expect(screen.getByText('本机')).toBeTruthy()
    // 3 台设备里只有 2 台可移除
    expect(screen.getAllByRole('button', { name: '移除设备' })).toHaveLength(2)
  })

  it('移除一台后列表少一行', async () => {
    await openAsLoggedIn()
    fireEvent.click(screen.getByRole('button', { name: /管理登录设备/ }))
    await screen.findByText(/移除设备后/)
    fireEvent.click(screen.getAllByRole('button', { name: '移除设备' })[0])
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: '移除设备' })).toHaveLength(1)
    })
  })
})

describe('屏 D1：验证码错误', () => {
  it('码错时 6 格整体转红并显示剩余次数', async () => {
    openPanel()
    await waitForA1()
    typePhone(SCENARIOS.existing)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    await screen.findByLabelText('验证码第 1 位')
    fillCode('000000')
    expect(await screen.findByText('验证码不正确，还可尝试 4 次')).toBeTruthy()
    expect(document.querySelector('.hub-otp.bad')).toBeTruthy()
  })
})

describe('屏 D2：发码被限频', () => {
  it('429 时显示黄条与按钮内倒计时，秒数取服务端 retryAfter', async () => {
    openPanel()
    await waitForA1()
    typePhone(SCENARIOS.rateLimited)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    expect(await screen.findByText(/发送过于频繁/)).toBeTruthy()
    // 48 来自 mock 的 retryAfter，不是前端拍的 60
    expect(screen.getByRole('button', { name: /重新获取（48s）/ })).toBeDisabled()
  })

  it('当日上限换成另一套文案，指向邮箱兜底', async () => {
    openPanel()
    await waitForA1()
    typePhone(SCENARIOS.dailyCap)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    expect(await screen.findByText(/今日发送次数已达上限/)).toBeTruthy()
  })
})

describe('屏 D3：无法连接', () => {
  it('上游不可达时转 D3，给重试与「暂不登录」两个出口', async () => {
    openPanel()
    await waitForA1()
    typePhone(SCENARIOS.offline)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    expect(await screen.findByText('暂时无法连接')).toBeTruthy()
    expect(screen.getByText('登录需要联网，本机功能不受影响')).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '暂不登录，继续使用' })).toBeTruthy()
  })

  // 回归：首次探测就失败时不能永久转圈。
  //
  // refresh() 的 catch 只 setFail('D3')、**不设 status**，所以 status 仍是 null。
  // body 选择若把 `status === null` 判在 `fail === 'D3'` 之前，界面就永远停在
  // 「正在检查登录状态…」——转圈转到底，renderOffline() 里的「重试」和
  // 「暂不登录，继续使用」两个出口一个都点不到，用户既看不到原因也退不出去。
  // 上面那条用例走的是**发码**失败，进 D3 时 status 已经探到了，遮不住这个次序问题。
  it('初次探测 /auth/status 就抛错时，显示错误屏而不是一直 loading', async () => {
    const api = await import('../../services/api')
    const spy = vi.spyOn(api, 'getAuthStatus').mockRejectedValue(new Error('network down'))
    try {
      openPanel()
      // 必须出现出口按钮；出现「正在检查登录状态…」则说明卡在 loading
      expect(await screen.findByRole('button', { name: '重试' })).toBeTruthy()
      expect(screen.getByRole('button', { name: '暂不登录，继续使用' })).toBeTruthy()
      expect(screen.queryByText('正在检查登录状态…')).toBeNull()
    } finally {
      spy.mockRestore()
    }
  })
})

/* 原型 D4：「成功后对话框直接关闭并回到工作台，侧栏账户区就地更新为已登录 ——
 * 不再插一屏『登录成功』」。实测时登录完停在了账户面板（C1），用户以为还有一步
 * 要做。C1 只该由「已登录后主动从侧栏点进来」这条路径到达。 */
describe('登录成功的落点', () => {
  /** 受控包装：`onClose` 真的把 `show` 置 false，和 UserHub 里的用法一致。
   *  用固定 `show` + mock onClose 测不出「窗关没关」—— 组件根本没被摘掉。 */
  const Controlled = ({ onToast }: { onToast: (m: string) => void }) => {
    const [open, setOpen] = useState(true)
    return <HubLoginPanel show={open} onClose={() => setOpen(false)} onToast={onToast} />
  }

  it('老用户验码成功后关窗并 toast，不停在账户面板', async () => {
    const onToast = vi.fn()
    render(<Controlled onToast={onToast} />)
    await waitForA1()
    typePhone(SCENARIOS.existing)
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    await screen.findByLabelText('验证码第 1 位')
    fillCode(MOCK_CODE)

    await waitFor(() => expect(onToast).toHaveBeenCalledWith('已登录'))
    // 账户面板的标志性内容不该留在屏上
    await waitFor(() => expect(screen.queryByText('登录方式')).toBeNull())
    expect(screen.queryByRole('button', { name: '退出登录' })).toBeNull()
  })

  it('新用户建号成功后同样关窗', async () => {
    const onToast = vi.fn()
    render(<Controlled onToast={onToast} />)
    await waitForA1()
    typePhone('13900001234') // 未注册 → 走建号
    fireEvent.click(screen.getByRole('button', { name: '获取验证码' }))
    await screen.findByLabelText('验证码第 1 位')
    fillCode(MOCK_CODE)

    // A3 建号屏
    const skip = await screen.findByRole('button', { name: '稍后设置' })
    fireEvent.click(skip)

    // 假后端 complete() 睡 700ms，再加两次 refresh 请求，超过 waitFor 默认 1s
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('账号已创建'), { timeout: 5000 })
    await waitFor(() => expect(screen.queryByText('登录方式')).toBeNull())
  })
})

/* 原有 describe('协议链接') 已删：登录屏上的协议文字与两个链接按团队决定整句去掉，
   守着的东西不存在了。`public/terms.html` 与 `public/privacy.html` 仍在包里，但界面
   上已无入口 —— 将来若挂回设置或关于页，对应的断言应写在那个组件的测试里。 */

/* 首屏硬门禁：登录是使用前置条件, 一个出口都不能有。
 *
 * 这几条守的是「门真的关得住」。少一条就漏一个绕过口 —— 之前是软门禁, 出口是
 * 一个显式按钮; 现在出口变成了「所有能关窗的通道都不该存在」, 而 ✕、遮罩点击、
 * Esc 分散在 HubDialog / UserHub 两处, 只看代码很容易漏掉其中一个。 */
describe('硬门禁：不可跳过', () => {
  it('mandatory 时 A1 上没有「暂不登录，继续使用」', async () => {
    render(<HubLoginPanel show mandatory onClose={() => {}} />)
    await waitForA1()
    expect(screen.queryByRole('button', { name: '暂不登录，继续使用' })).toBeNull()
  })

  it('mandatory 时没有 ✕，点遮罩也不关窗', async () => {
    const onClose = vi.fn()
    render(<HubLoginPanel show mandatory onClose={onClose} />)
    await waitForA1()
    expect(screen.queryByRole('button', { name: '关闭' })).toBeNull()
    // 遮罩退化为 aria-hidden 的装饰层：既没有可点的角色, 也不该触发 onClose
    const backdrop = document.querySelector('.hub-dialog-backdrop')
    expect(backdrop?.getAttribute('aria-hidden')).toBe('true')
    if (backdrop) fireEvent.click(backdrop)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('mandatory 时断网屏也不放行，只给重试', async () => {
    const api = await import('../../services/api')
    const spy = vi.spyOn(api, 'getAuthStatus').mockRejectedValue(new Error('network down'))
    try {
      render(<HubLoginPanel show mandatory onClose={() => {}} />)
      expect(await screen.findByRole('button', { name: '重试' })).toBeTruthy()
      expect(screen.queryByRole('button', { name: '暂不登录，继续使用' })).toBeNull()
      // 得说清为什么退不出去, 否则就是一堵没有解释的墙
      expect(screen.getByText(/登录后才能使用/)).toBeTruthy()
      // 硬门禁下不能再说「本机功能不受影响」—— 那时人是真进不去
      expect(screen.queryByText('登录需要联网，本机功能不受影响')).toBeNull()
      expect(screen.getByText('登录需要联网，请检查网络后重试')).toBeTruthy()
    } finally {
      spy.mockRestore()
    }
  })

  it('非 mandatory（用户自己点开）时不显示该出口，但 ✕ 在', async () => {
    openPanel()
    await waitForA1()
    expect(screen.queryByRole('button', { name: '暂不登录，继续使用' })).toBeNull()
    // ✕ 与可点遮罩都在（两者 aria-label 都是「关闭」）
    expect(screen.getAllByRole('button', { name: '关闭' })).toHaveLength(2)
  })
})

describe('登出后必须重新被拦住（回归）', () => {
  /* 这条守的是已经发出去过的一个洞：硬门禁由父层按 /auth/status 判定，而**登出发生在
     本面板内部**。不往上通知一声，父层的 authGate 会一直停在启动那次探到的 passed，
     于是登出后登录窗上冒出 ✕、点遮罩也能关掉 —— 门只在冷启动那一下存在。 */
  it('登出成功后回调父层，让它重判门禁', async () => {
    const onLoginStateChanged = vi.fn()
    // 从已登录态起步：登录成功那条路径是关窗回工作台，不停在 C1，铺垫不能走它
    seedLoggedIn()
    render(<HubLoginPanel show onClose={() => {}} onLoginStateChanged={onLoginStateChanged} />)
    await screen.findByText('已登录')

    expect(onLoginStateChanged).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '退出登录' }))
    // 回调必须发生在面板已经回到输入屏之后, 否则父层置 mandatory 时会闪一下账户面板
    await waitFor(() => expect(onLoginStateChanged).toHaveBeenCalled())
    expect(await waitForA1()).toBeTruthy()
  })

  it('父层随后把 mandatory 置真时，✕ 与可点遮罩都消失', async () => {
    // 模拟父层重判门禁的结果：同一个面板从非 mandatory 变 mandatory
    const { rerender } = render(<HubLoginPanel show onClose={() => {}} />)
    await waitForA1()
    expect(screen.getAllByRole('button', { name: '关闭' })).toHaveLength(2)

    rerender(<HubLoginPanel show mandatory onClose={() => {}} />)
    await waitForA1()
    expect(screen.queryByRole('button', { name: '关闭' })).toBeNull()
  })
})
