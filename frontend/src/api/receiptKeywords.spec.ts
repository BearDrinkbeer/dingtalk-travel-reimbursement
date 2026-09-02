import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  createReceiptKeyword,
  deleteReceiptKeyword,
  listReceiptKeywords,
  updateReceiptKeyword,
} from '@/api/receiptKeywords'
import { http } from '@/api/http'

vi.mock('@/api/http', () => ({
  http: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

const mapping = {
  id: 1,
  keyword: '文具',
  categoryId: 'office',
  categoryName: '办公费',
}

describe('receipt keyword API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('uses the administrator CRUD endpoints', async () => {
    vi.mocked(http.get).mockResolvedValue({ data: { data: [mapping] } })
    vi.mocked(http.post).mockResolvedValue({ data: { data: mapping } })
    vi.mocked(http.put).mockResolvedValue({ data: { data: mapping } })
    vi.mocked(http.delete).mockResolvedValue({ data: { data: {} } })

    await expect(listReceiptKeywords()).resolves.toEqual([mapping])
    await expect(createReceiptKeyword({ keyword: '文具', categoryId: 'office' }))
      .resolves.toEqual(mapping)
    await expect(updateReceiptKeyword(1, { keyword: '文具', categoryId: 'office' }))
      .resolves.toEqual(mapping)
    await expect(deleteReceiptKeyword(1)).resolves.toBeUndefined()

    expect(http.get).toHaveBeenCalledWith('/admin/receipt-keywords')
    expect(http.post).toHaveBeenCalledWith('/admin/receipt-keywords', {
      keyword: '文具', categoryId: 'office',
    })
    expect(http.put).toHaveBeenCalledWith('/admin/receipt-keywords/1', {
      keyword: '文具', categoryId: 'office',
    })
    expect(http.delete).toHaveBeenCalledWith('/admin/receipt-keywords/1')
  })
})
