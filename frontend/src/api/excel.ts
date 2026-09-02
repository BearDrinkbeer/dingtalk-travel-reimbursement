import axios from 'axios'

import { http } from './http'
import type { ExcelGeneratePayload } from '@/types/expenses'

export const DEFAULT_EXCEL_FILENAME = '差旅费报销单.xlsx'

function safeDownloadedFilename(value: string, fallback: string): string {
  const leaf = value
    .split(/[\\/]/)
    .at(-1)
    ?.split('')
    .filter((character) => {
      const code = character.charCodeAt(0)
      return code > 31 && code !== 127
    })
    .join('')
    .trim()
  if (!leaf || !leaf.toLowerCase().endsWith('.xlsx')) return fallback
  return leaf
}

export function filenameFromContentDisposition(
  header: string | undefined,
  fallback = DEFAULT_EXCEL_FILENAME,
): string {
  if (!header) return fallback
  const encoded = header.match(/filename\*\s*=\s*UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try {
      return safeDownloadedFilename(decodeURIComponent(encoded.trim()), fallback)
    } catch {
      return fallback
    }
  }
  const quoted = header.match(/filename\s*=\s*"([^"]+)"/i)?.[1]
  const plain = quoted ?? header.match(/filename\s*=\s*([^;]+)/i)?.[1]
  return plain ? safeDownloadedFilename(plain.trim(), fallback) : fallback
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  try {
    anchor.href = url
    anchor.download = filename
    anchor.hidden = true
    document.body.append(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    URL.revokeObjectURL(url)
  }
}

export async function generateExpenseExcel(
  payload: ExcelGeneratePayload,
): Promise<{ blob: Blob; filename: string }> {
  const response = await http.post<Blob>('/excel/generate', payload, {
    responseType: 'blob',
    timeout: 30_000,
  })
  return {
    blob: response.data,
    filename: filenameFromContentDisposition(response.headers['content-disposition']),
  }
}

export async function generateAndDownloadExpenseExcel(payload: ExcelGeneratePayload): Promise<void> {
  const result = await generateExpenseExcel(payload)
  downloadBlob(result.blob, result.filename)
}

export async function excelDownloadErrorMessage(error: unknown): Promise<string> {
  if (!axios.isAxiosError(error)) return 'Excel 生成失败，请稍后重试'
  const data = error.response?.data
  if (data instanceof Blob && data.type.includes('json')) {
    try {
      const parsed = JSON.parse(await data.text()) as { error?: { message?: string } }
      return parsed.error?.message || 'Excel 生成失败，请检查填写内容'
    } catch {
      return 'Excel 生成失败，请检查填写内容'
    }
  }
  return (
    (data as { error?: { message?: string } } | undefined)?.error?.message ??
    'Excel 生成失败，请检查填写内容'
  )
}
