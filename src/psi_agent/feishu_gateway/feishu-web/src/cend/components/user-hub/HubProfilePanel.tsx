import { useEffect, useState } from 'react'
import { Upload } from 'lucide-react'
import HubDialog from './HubDialog'
import {
  readAvatarDataUrl,
  readStoredAvatar,
  readStoredName,
  writeStoredProfile,
} from '../../services/userProfile'

type Props = {
  show: boolean
  onClose: () => void
  onSaved?: (name: string, avatar: string) => void
  onToast?: (message: string) => void
}

export default function HubProfilePanel({ show, onClose, onSaved, onToast }: Props) {
  const [name, setName] = useState('')
  const [avatar, setAvatar] = useState('')

  useEffect(() => {
    if (!show) return
    setName(readStoredName())
    setAvatar(readStoredAvatar())
  }, [show])

  const initial = name.trim().charAt(0).toUpperCase()

  const onFile = async (file: File | null) => {
    if (!file) return
    try {
      setAvatar(await readAvatarDataUrl(file))
    } catch (e) {
      onToast?.(e instanceof Error ? e.message : '上传失败')
    }
  }

  const save = () => {
    writeStoredProfile(name, avatar)
    onSaved?.(name.trim(), avatar)
    onClose()
  }

  return (
    <HubDialog
      show={show}
      title="我的资料"
      width={440}
      onClose={onClose}
      actions={(
        <>
          <button type="button" className="hub-btn ghost" onClick={onClose}>取消</button>
          <button type="button" className="hub-btn primary" onClick={save}>保存</button>
        </>
      )}
    >
      <div className="hub-profile-avatar-row">
        <div className="hub-profile-preview" aria-hidden="true">
          {avatar ? <img src={avatar} alt="" /> : initial ? <span>{initial}</span> : <span className="hub-profile-fallback">?</span>}
        </div>
        <div className="hub-profile-avatar-actions">
          <label className="hub-btn ghost upload">
            <input
              type="file"
              accept="image/*"
              hidden
              onChange={(e) => {
                void onFile(e.target.files?.[0] ?? null)
                e.target.value = ''
              }}
            />
            <Upload size={16} /> 上传头像
          </label>
          {avatar ? (
            <button type="button" className="hub-link" onClick={() => setAvatar('')}>移除头像</button>
          ) : null}
        </div>
      </div>
      <label className="hub-field">
        <span>称呼</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="希望 HaiTun 怎么称呼您？"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              save()
            }
          }}
        />
      </label>
    </HubDialog>
  )
}
