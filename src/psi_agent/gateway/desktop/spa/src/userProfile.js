/** localStorage keys for Web Console user profile (UI-only, not workspace USER.md). */
export const LS_USER_NAME = 'gw-user-name'
export const LS_USER_AVATAR = 'gw-user-avatar'

/** @param {File} file @returns {Promise<string>} data URL */
export function readAvatarDataUrl(file) {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith('image/')) {
      reject(new Error('请选择图片文件'))
      return
    }
    if (file.size > 512 * 1024) {
      reject(new Error('图片请小于 512KB'))
      return
    }
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(file)
  })
}
