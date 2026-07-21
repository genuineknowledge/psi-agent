import HubDialog from './HubDialog'

type Props = {
  show: boolean
  onClose: () => void
}

export default function HubLoginPanel({ show, onClose }: Props) {
  return (
    <HubDialog
      show={show}
      title="登录"
      width={400}
      onClose={onClose}
      actions={<button type="button" className="hub-btn primary" onClick={onClose}>知道了</button>}
    >
      <p className="hub-login-body">
        当前为<strong>本地 Gateway 模式</strong>，无需登录即可使用 Web 控制台。
      </p>
      <p className="hub-login-hint">账号登录与云端同步将在后续版本提供。</p>
    </HubDialog>
  )
}
