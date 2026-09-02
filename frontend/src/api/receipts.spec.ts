import { beforeEach, describe, expect, it, vi } from 'vitest'

import { http } from '@/api/http'
import {
  deleteReceiptFile,
  recognizeReceiptFile,
  uploadReceiptFile,
} from '@/api/receipts'

vi.mock('@/api/http', () => ({
  http: { post: vi.fn(), delete: vi.fn() },
}))

describe('receipt API', () => {
  beforeEach(() => {
    vi.mocked(http.post).mockReset()
    vi.mocked(http.delete).mockReset()
  })

  it('uses the backend multipart field name without setting a manual content type', async () => {
    vi.mocked(http.post).mockResolvedValue({
      data: { data: { files: [{ id: 'temp-1', name: '票据.jpg', status: 'uploaded' }] } },
    })
    const file = new File(['image'], '票据.jpg', { type: 'image/jpeg' })

    await uploadReceiptFile(file)

    const call = vi.mocked(http.post).mock.calls[0]
    expect(call?.[0]).toBe('/files/upload')
    expect(call?.[1]).toBeInstanceOf(FormData)
    expect((call?.[1] as FormData).getAll('files[]')).toEqual([file])
    expect((call?.[1] as FormData).getAll('files')).toEqual([])
    expect(call?.[2]).not.toHaveProperty('headers.Content-Type')
  })

  it('sends only opaque file ids and optional trip year to OCR', async () => {
    vi.mocked(http.post).mockResolvedValue({ data: { data: { items: [] } } })

    await recognizeReceiptFile('temp-a', { tripYear: 2026 })

    expect(http.post).toHaveBeenCalledWith(
      '/ocr',
      { fileIds: ['temp-a'], tripYear: 2026 },
      expect.objectContaining({ timeout: 135_000 }),
    )
  })

  it('URL-encodes the opaque id when deleting', async () => {
    vi.mocked(http.delete).mockResolvedValue({ data: { data: {} } })
    await deleteReceiptFile('temporary/id')
    expect(http.delete).toHaveBeenCalledWith('/files/temporary%2Fid', { signal: undefined })
  })
})
