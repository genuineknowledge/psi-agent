import type { ChatFile } from '../haitun-agent/model'

/** Strip optional `data:*;base64,` prefix before atob. */
export function rawBase64(data: string): string {
  return data.includes(',') ? data.split(',')[1]! : data
}

export function chatFileToFile(file: ChatFile): File {
  const b64 = rawBase64(file.data)
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new File([bytes], file.name)
}

export function appendChatFilesToFormData(fd: FormData, files: Array<File | ChatFile>): void {
  for (const f of files) {
    if (f instanceof File) {
      fd.append('file', f, f.name)
    } else if (f?.data && f?.name) {
      fd.append('file', chatFileToFile(f), f.name)
    }
  }
}

export async function fileToChatFile(file: File): Promise<ChatFile> {
  const data = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.readAsDataURL(file)
  })
  return { name: file.name, data }
}

export async function filesToChatFiles(files: File[]): Promise<ChatFile[]> {
  return Promise.all(files.map(fileToChatFile))
}
