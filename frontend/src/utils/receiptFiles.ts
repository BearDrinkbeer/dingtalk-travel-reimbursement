import type { ReceiptUploadLimits } from '@/types/auth'

export const DEFAULT_RECEIPT_LIMITS: Readonly<ReceiptUploadLimits> = {
  maxFiles: 200,
  maxFileBytes: 20 * 1024 * 1024,
  maxSessionBytes: 100 * 1024 * 1024,
}

const MIME_BY_EXTENSION: Readonly<Record<string, string>> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  pdf: 'application/pdf',
}

export interface RejectedReceiptFile {
  file: File
  message: string
}

export interface ReceiptFileValidation {
  accepted: File[]
  rejected: RejectedReceiptFile[]
}

function extensionOf(name: string): string {
  const position = name.lastIndexOf('.')
  return position < 0 ? '' : name.slice(position + 1).toLowerCase()
}

function fileError(file: File, limits: ReceiptUploadLimits): string | null {
  if (!Number.isSafeInteger(file.size) || file.size <= 0) return '文件为空或大小无效'
  if (file.size > limits.maxFileBytes) {
    return `单个文件不能超过 ${formatFileSize(limits.maxFileBytes)}`
  }
  const expectedMime = MIME_BY_EXTENSION[extensionOf(file.name)]
  if (!expectedMime) return '仅支持 JPG、JPEG、PNG 或单页 PDF'
  if (file.type && file.type.toLowerCase() !== expectedMime) return '文件扩展名与浏览器识别类型不一致'
  return null
}

export function validateReceiptFiles(
  selected: readonly File[],
  currentFileCount = 0,
  currentFileBytes = 0,
  limits: ReceiptUploadLimits = DEFAULT_RECEIPT_LIMITS,
): ReceiptFileValidation {
  const accepted: File[] = []
  const rejected: RejectedReceiptFile[] = []
  let retainedBytes = Math.max(0, currentFileBytes)
  let availableSlots = Math.max(0, limits.maxFiles - currentFileCount)

  for (const file of selected) {
    const validationError = fileError(file, limits)
    if (validationError) {
      rejected.push({ file, message: validationError })
      continue
    }
    if (availableSlots === 0) {
      rejected.push({ file, message: `当前最多保留 ${limits.maxFiles} 个待处理票据文件` })
      continue
    }
    if (retainedBytes + file.size > limits.maxSessionBytes) {
      rejected.push({
        file,
        message: `当前会话保留的票据总大小不能超过 ${formatFileSize(limits.maxSessionBytes)}`,
      })
      continue
    }
    accepted.push(file)
    retainedBytes += file.size
    availableSlots -= 1
  }
  return { accepted, rejected }
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Number((bytes / 1024).toFixed(1))} KiB`
  return `${Number((bytes / (1024 * 1024)).toFixed(1))} MiB`
}
