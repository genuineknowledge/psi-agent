/** localStorage keys — same as spa v1 (`gw-user-*`). */
export const LS_USER_NAME = 'gw-user-name'
export const LS_USER_AVATAR = 'gw-user-avatar'
/** Same-tab notification after saving profile (storage/focus do not fire locally). */
export const USER_PROFILE_EVENT = 'psi:user-profile-updated'

export function readStoredName(): string {
  try {
    return window.localStorage.getItem(LS_USER_NAME)?.trim() || ''
  } catch {
    return ''
  }
}

export function readStoredAvatar(): string {
  try {
    return window.localStorage.getItem(LS_USER_AVATAR) || ''
  } catch {
    return ''
  }
}

export function writeStoredProfile(name: string, avatar: string) {
  window.localStorage.setItem(LS_USER_NAME, name.trim())
  window.localStorage.setItem(LS_USER_AVATAR, avatar)
  window.dispatchEvent(new Event(USER_PROFILE_EVENT))
}

export function readAvatarDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith('image/')) {
      reject(new Error('请选择图片文件'))
      return
    }
    if (file.size > 3 * 1024 * 1024) {
      reject(new Error('图片请小于 3MB'))
      return
    }
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(file)
  })
}
