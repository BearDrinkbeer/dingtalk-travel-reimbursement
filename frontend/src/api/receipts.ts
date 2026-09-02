import type { AxiosProgressEvent } from 'axios'

import { http } from './http'
import type { ApiEnvelope } from '@/types/auth'
import type { OcrReceiptCandidate, UploadedReceipt } from '@/types/receipts'

interface UploadReceiptOptions {
  signal?: AbortSignal
  onProgress?: (percent: number) => void
}

interface RecognizeReceiptOptions {
  tripYear?: number
  signal?: AbortSignal
}

function uploadPercent(event: AxiosProgressEvent): number {
  if (!event.total || event.total <= 0) return 0
  return Math.min(100, Math.max(0, Math.round((event.loaded / event.total) * 100)))
}

export async function uploadReceiptFile(
  file: File,
  options: UploadReceiptOptions = {},
): Promise<UploadedReceipt> {
  const form = new FormData()
  form.append('files[]', file, file.name)
  const response = await http.post<ApiEnvelope<{ files: UploadedReceipt[] }>>(
    '/files/upload',
    form,
    {
      signal: options.signal,
      timeout: 120_000,
      onUploadProgress: (event) => options.onProgress?.(uploadPercent(event)),
    },
  )
  const files = response.data.data.files
  if (files.length !== 1 || !files[0]) throw new Error('上传结果无效，请重新选择该文件')
  return files[0]
}

export async function deleteReceiptFile(tempId: string, signal?: AbortSignal): Promise<void> {
  await http.delete(`/files/${encodeURIComponent(tempId)}`, { signal })
}

export async function recognizeReceiptFile(
  fileId: string,
  options: RecognizeReceiptOptions = {},
): Promise<OcrReceiptCandidate[]> {
  const body: { fileIds: [string]; tripYear?: number } = { fileIds: [fileId] }
  if (options.tripYear !== undefined) body.tripYear = options.tripYear
  const response = await http.post<ApiEnvelope<{ items: OcrReceiptCandidate[] }>>('/ocr', body, {
    signal: options.signal,
    timeout: 135_000,
  })
  return response.data.data.items
}
