import { beforeEach, describe, expect, it, vi } from 'vitest'

import { calculateTotals } from '@/api/expenses'
import { http } from '@/api/http'
import type { ExcelExpenseItemInput } from '@/types/expenses'

vi.mock('@/api/http', () => ({
  http: {
    post: vi.fn(),
  },
}))

describe('expense calculation API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sends only calculation fields when a draft item carries provenance', async () => {
    vi.mocked(http.post).mockResolvedValue({
      data: {
        data: {
          expenseTotal: '44.89',
          subsidyTotal: '0.00',
          totalAmount: '44.89',
          receiptCount: 1,
          uppercaseAmount: '肆拾肆元捌角玖分',
          subsidy: null,
        },
      },
    })
    const draftItem = {
      id: 'ocr-file-1',
      source: 'ocr',
      sourceFileId: 'file-1',
      confidence: '0.95',
      warnings: [],
      category: 'local_transport',
      date: '2026-09-01',
      displayDate: '2026-09-01',
      description: '机场至酒店',
      amount: '44.89',
      receiptCount: 1,
    } satisfies ExcelExpenseItemInput & Record<string, unknown>

    await calculateTotals(null, [draftItem])

    expect(http.post).toHaveBeenCalledWith('/calculate/totals', {
      trip: null,
      items: [{
        category: 'local_transport',
        date: '2026-09-01',
        displayDate: '2026-09-01',
        description: '机场至酒店',
        amount: '44.89',
        receiptCount: 1,
      }],
    })
  })
})
