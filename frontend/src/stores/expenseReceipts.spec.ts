import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { calculateTotals, getExpenseCategories } from '@/api/expenses'
import {
  deleteReceiptFile,
  recognizeReceiptFile,
  uploadReceiptFile,
} from '@/api/receipts'
import { useExpenseStore } from '@/stores/expense'
import type { OcrReceiptCandidate } from '@/types/receipts'

const CATEGORIES = [
  { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
  { id: 'other', name: '其他', order: 17, manualSelectable: true },
]

vi.mock('@/api/expenses', () => ({
  calculateTotals: vi.fn(),
  getExpenseCategories: vi.fn(),
}))

vi.mock('@/api/receipts', () => ({
  deleteReceiptFile: vi.fn(),
  recognizeReceiptFile: vi.fn(),
  uploadReceiptFile: vi.fn(),
}))

function file(name: string): File {
  return new File(['receipt'], name, {
    type: name.endsWith('.pdf') ? 'application/pdf' : 'image/jpeg',
  })
}

function recognized(
  fileId: string,
  overrides: Partial<OcrReceiptCandidate> = {},
): OcrReceiptCandidate {
  return {
    fileId,
    type: 'train',
    categoryId: 'rail_fare',
    categoryName: '火车票',
    date: '2026-06-30',
    description: '北京南-合肥南',
    amount: '454.00',
    receiptCount: 1,
    source: 'ocr',
    confidence: '0.93',
    warnings: [],
    status: 'recognized',
    error: null,
    ...overrides,
  }
}

function failed(fileId: string, code: string, message: string): OcrReceiptCandidate {
  return {
    ...recognized(fileId),
    categoryId: 'other',
    categoryName: '其他',
    date: null,
    description: null,
    amount: null,
    confidence: '0.00',
    warnings: ['MANUAL_REVIEW_REQUIRED'],
    status: 'failed',
    error: { code, message },
  }
}

function responseError(code: string, message = '文件无效'): Error {
  return Object.assign(new Error(message), {
    isAxiosError: true,
    response: { status: 400, data: { error: { code, message } } },
  })
}

describe('expense receipt flow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(calculateTotals).mockReset()
    vi.mocked(getExpenseCategories).mockReset()
    vi.mocked(uploadReceiptFile).mockReset()
    vi.mocked(recognizeReceiptFile).mockReset()
    vi.mocked(deleteReceiptFile).mockReset().mockResolvedValue()
  })

  it('reconciles OCR by fileId and directly creates editable expense items', async () => {
    vi.mocked(uploadReceiptFile)
      .mockResolvedValueOnce({ id: 'temp-a', name: 'a.jpg', status: 'uploaded' })
      .mockResolvedValueOnce({ id: 'temp-b', name: 'b.jpg', status: 'uploaded' })
    vi.mocked(recognizeReceiptFile)
      .mockResolvedValueOnce([recognized('temp-a')])
      .mockResolvedValueOnce([failed('temp-b', 'OCR_FAILED', '无法识别，请手工填写')])
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('a.jpg'), file('b.jpg')])

    expect(store.receiptFiles.map((entry) => [entry.tempId, entry.status])).toEqual([
      ['temp-a', 'done'],
      ['temp-b', 'done'],
    ])
    expect(store.receiptFiles[0]?.candidate).toMatchObject({ amount: '454.00' })
    expect(store.items).toHaveLength(2)
    expect(store.items[0]).toMatchObject({ amount: '454.00', source: 'ocr' })
    expect(store.items[1]).toMatchObject({ amount: '', source: 'ocr' })
    expect(recognizeReceiptFile).toHaveBeenNthCalledWith(
      1,
      'temp-a',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(recognizeReceiptFile).toHaveBeenNthCalledWith(
      2,
      'temp-b',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('uploads selected files in sequential one-file requests and continues after a failure', async () => {
    vi.mocked(uploadReceiptFile)
      .mockRejectedValueOnce(responseError('FILE_TYPE_MISMATCH'))
      .mockResolvedValueOnce({ id: 'temp-good', name: 'good.jpg', status: 'uploaded' })
    vi.mocked(recognizeReceiptFile).mockResolvedValue([recognized('temp-good')])
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('bad.jpg'), file('good.jpg')])

    expect(uploadReceiptFile).toHaveBeenCalledTimes(2)
    expect(uploadReceiptFile).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ name: 'bad.jpg' }),
      expect.anything(),
    )
    expect(uploadReceiptFile).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ name: 'good.jpg' }),
      expect.anything(),
    )
    expect(store.receiptFiles.map((entry) => entry.status)).toEqual(['failed', 'done'])
    expect(store.items).toHaveLength(1)
  })

  it('does not start the next selected file request before the current file finishes', async () => {
    let finishFirst: ((value: { id: string; name: string; status: 'uploaded' }) => void) | undefined
    vi.mocked(uploadReceiptFile)
      .mockImplementationOnce(() => new Promise((resolve) => {
        finishFirst = resolve
      }))
      .mockResolvedValueOnce({ id: 'temp-b', name: 'b.jpg', status: 'uploaded' })
    vi.mocked(recognizeReceiptFile)
      .mockResolvedValueOnce([recognized('temp-a')])
      .mockResolvedValueOnce([recognized('temp-b')])
    const store = useExpenseStore()
    store.categories = CATEGORIES

    const operation = store.addReceiptFiles([file('a.jpg'), file('b.jpg')])
    await Promise.resolve()
    expect(uploadReceiptFile).toHaveBeenCalledTimes(1)

    finishFirst?.({ id: 'temp-a', name: 'a.jpg', status: 'uploaded' })
    await operation
    expect(uploadReceiptFile).toHaveBeenCalledTimes(2)
    expect(store.receiptFiles.map((entry) => entry.tempId)).toEqual(['temp-a', 'temp-b'])
  })

  it('maps each authentication upload failure independently without unknown temp ids', async () => {
    vi.mocked(uploadReceiptFile).mockRejectedValue(responseError('CSRF_INVALID'))
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('a.jpg'), file('b.jpg')])

    expect(uploadReceiptFile).toHaveBeenCalledTimes(2)
    expect(store.receiptFiles.every((entry) => entry.status === 'failed')).toBe(true)
    expect(store.receiptFiles.every((entry) => entry.tempId === undefined)).toBe(true)
  })

  it('continues one-file requests after a retained quota failure', async () => {
    vi.mocked(uploadReceiptFile).mockRejectedValue(responseError('SESSION_STORAGE_LIMIT'))
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('a.jpg'), file('b.jpg')])

    expect(uploadReceiptFile).toHaveBeenCalledTimes(2)
    expect(store.receiptFiles.every((entry) => entry.status === 'failed')).toBe(true)
  })

  it('keeps incomplete OCR as an editable item and clears field warnings after editing', async () => {
    vi.mocked(uploadReceiptFile).mockResolvedValue({
      id: 'temp-a', name: 'a.jpg', status: 'uploaded',
    })
    vi.mocked(recognizeReceiptFile)
      .mockResolvedValueOnce([recognized('temp-a', {
        amount: null,
        date: null,
        warnings: [
          'QR_AMOUNT_MISMATCH',
          'QR_ISSUE_DATE_USED',
          'INVOICE_DATE_USED_AS_OCCURRENCE',
        ],
      })])
      .mockResolvedValueOnce([recognized('temp-a', { amount: '99.10' })])
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('a.jpg')])
    const receipt = store.receiptFiles[0]!
    expect(receipt.status).toBe('done')
    expect(receipt.candidate).toMatchObject({ amount: '', date: '' })
    expect(receipt.candidate?.warnings).toEqual(expect.arrayContaining([
      'MISSING_AMOUNT', 'MISSING_DATE', 'MANUAL_REVIEW_REQUIRED',
    ]))
    expect(store.items).toHaveLength(1)
    expect(store.items[0]).toMatchObject({ amount: '', date: undefined, source: 'ocr' })
    expect(store.itemReadinessError).toContain('发生日期')

    store.upsertManualItem({
      id: store.items[0]!.id,
      category: 'rail_fare',
      date: '2026-06-30',
      displayDate: '2026-06-30',
      description: '手工修正后的路线',
      amount: '12.34',
      receiptCount: 1,
      warnings: [],
    })
    expect(store.items[0]).toMatchObject({
      amount: '12.34', date: '2026-06-30', source: 'ocr',
    })
    expect(store.items[0]?.warnings).not.toEqual(expect.arrayContaining([
      'MISSING_AMOUNT', 'MISSING_DATE', 'MANUAL_REVIEW_REQUIRED',
      'QR_AMOUNT_MISMATCH', 'QR_ISSUE_DATE_USED', 'INVOICE_DATE_USED_AS_OCCURRENCE',
    ]))

    await store.retryReceipt(receipt.localId)
    expect(store.items).toHaveLength(1)
    expect(store.items[0]).toMatchObject({ id: 'ocr-temp-a', amount: '99.10' })
    expect(receipt.status).toBe('done')
    expect(receipt.candidate).toMatchObject({ amount: '99.10' })
  })

  it('explicit retry replaces the linked OCR item without creating a duplicate', async () => {
    vi.mocked(uploadReceiptFile).mockResolvedValue({
      id: 'temp-a', name: 'a.jpg', status: 'uploaded',
    })
    vi.mocked(recognizeReceiptFile)
      .mockResolvedValueOnce([recognized('temp-a', { amount: '10.00' })])
      .mockResolvedValueOnce([recognized('temp-a', { amount: '20.00' })])
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('a.jpg')])
    const receipt = store.receiptFiles[0]!
    expect(store.items[0]?.amount).toBe('10.00')

    await store.retryReceipt(receipt.localId)
    expect(store.items).toHaveLength(1)
    expect(store.items[0]?.amount).toBe('20.00')
    expect(receipt.candidate?.amount).toBe('20.00')
  })

  it('adds every OCR result directly, including rows that still need editing', async () => {
    vi.mocked(uploadReceiptFile)
      .mockResolvedValueOnce({ id: 'temp-a', name: 'a.jpg', status: 'uploaded' })
      .mockResolvedValueOnce({ id: 'temp-b', name: 'b.jpg', status: 'uploaded' })
      .mockResolvedValueOnce({ id: 'temp-c', name: 'c.jpg', status: 'uploaded' })
    vi.mocked(recognizeReceiptFile)
      .mockResolvedValueOnce([recognized('temp-a')])
      .mockResolvedValueOnce([recognized('temp-b', { date: null })])
      .mockResolvedValueOnce([recognized('temp-c', { amount: '88.00' })])
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('a.jpg'), file('b.jpg'), file('c.jpg')])
    expect(store.items).toHaveLength(3)
    expect(store.items.map((item) => item.id)).toEqual([
      'ocr-temp-a', 'ocr-temp-b', 'ocr-temp-c',
    ])
    expect(store.items[1]).toMatchObject({ date: undefined, source: 'ocr' })
    expect(store.receiptFiles.every((receipt) => receipt.status === 'done')).toBe(true)
  })

  it('removes an OCR expense item and its temporary file together', async () => {
    vi.mocked(uploadReceiptFile).mockResolvedValue({
      id: 'temp-a', name: 'wrong.jpg', status: 'uploaded',
    })
    vi.mocked(recognizeReceiptFile).mockResolvedValue([recognized('temp-a')])
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('wrong.jpg')])

    await expect(store.removeExpenseItem(store.items[0]!.id)).resolves.toBe(true)
    expect(store.receiptFiles).toEqual([])
    expect(store.items).toEqual([])
    expect(deleteReceiptFile).toHaveBeenCalledWith('temp-a')
  })

  it('removes the linked expense item even when temporary cleanup must fall back to TTL', async () => {
    vi.mocked(uploadReceiptFile).mockResolvedValue({
      id: 'temp-a', name: 'wrong.jpg', status: 'uploaded',
    })
    vi.mocked(recognizeReceiptFile).mockResolvedValue([recognized('temp-a')])
    vi.mocked(deleteReceiptFile).mockRejectedValue(new Error('temporary network failure'))
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('wrong.jpg')])
    const receipt = store.receiptFiles[0]!
    expect(store.items).toHaveLength(1)

    await expect(store.removeReceipt(receipt.localId)).resolves.toBe(true)
    expect(store.receiptFiles).toEqual([])
    expect(store.items).toEqual([])
    expect(deleteReceiptFile).toHaveBeenCalledWith('temp-a')
  })

  it('keeps manual entry available when local OCR is disabled', async () => {
    vi.mocked(uploadReceiptFile).mockResolvedValue({
      id: 'temp-a', name: 'a.jpg', status: 'uploaded',
    })
    vi.mocked(recognizeReceiptFile).mockResolvedValue([
      failed('temp-a', 'OCR_DISABLED', '本地 OCR 尚未配置，可手工填写票据信息'),
    ])
    const store = useExpenseStore()
    store.categories = CATEGORIES

    await store.addReceiptFiles([file('a.jpg')])
    expect(store.ocrUnavailable).toBe(true)
    expect(store.items).toHaveLength(1)
    expect(store.items[0]).toMatchObject({ amount: '', source: 'ocr' })
    expect(() => store.upsertManualItem({
      category: 'other',
      date: '2026-06-30',
      displayDate: '2026-06-30',
      description: '手工补录',
      amount: '10',
      receiptCount: 1,
    })).not.toThrow()
  })

  it('includes a complete OCR item in totals and Excel without a confirmation step', async () => {
    vi.mocked(uploadReceiptFile).mockResolvedValue({
      id: 'temp-a', name: 'a.jpg', status: 'uploaded',
    })
    vi.mocked(recognizeReceiptFile).mockResolvedValue([recognized('temp-a')])
    vi.mocked(calculateTotals).mockResolvedValue({
      expenseTotal: '454.00',
      subsidyTotal: '100.00',
      totalAmount: '554.00',
      receiptCount: 1,
      uppercaseAmount: '伍佰伍拾肆元整',
      subsidy: {
        tripType: 'business', calendarDays: 1, effectiveDays: '1.0',
        dailyRate: '100.00', total: '100.00',
      },
    })
    const store = useExpenseStore()
    store.categories = CATEGORIES
    store.manualProjectText = 'P-007 测试项目'
    store.setSubsidyIncluded(true)
    Object.assign(store.trip, {
      startDate: '2026-06-30', startTime: '09:00',
      endDate: '2026-06-30', endTime: '18:00',
    })
    await store.addReceiptFiles([file('a.jpg')])
    await store.refreshCalculations()

    expect(calculateTotals).toHaveBeenLastCalledWith(
      expect.anything(),
      [expect.objectContaining({ amount: '454.00', category: 'rail_fare' })],
    )
    expect(vi.mocked(calculateTotals).mock.lastCall?.[1]?.[0]).toHaveProperty('source', 'ocr')

    const payload = store.buildExcelPayload()
    expect(payload?.items).toEqual([{
      category: 'rail_fare',
      date: '2026-06-30',
      displayDate: '2026-06-30',
      description: '北京南-合肥南',
      amount: '454.00',
      receiptCount: 1,
    }])
    const serialized = JSON.stringify(payload)
    expect(serialized).not.toContain('temp-a')
    expect(serialized).not.toContain('fileId')
    expect(serialized).not.toContain('raw')
    expect(serialized).not.toContain('employee')
  })

  it('clears files and candidates on reset and ignores a late upload result', async () => {
    let finishUpload: ((value: { id: string; name: string; status: 'uploaded' }) => void) | undefined
    vi.mocked(uploadReceiptFile).mockImplementation(() => new Promise((resolve) => {
      finishUpload = resolve
    }))
    const store = useExpenseStore()
    store.categories = CATEGORIES
    const operation = store.addReceiptFiles([file('a.jpg')])
    await Promise.resolve()

    store.reset()
    finishUpload?.({ id: 'temp-late', name: 'a.jpg', status: 'uploaded' })
    await operation

    expect(store.receiptFiles).toEqual([])
    expect(store.items).toEqual([])
    expect(recognizeReceiptFile).not.toHaveBeenCalled()
    expect(deleteReceiptFile).toHaveBeenCalledWith('temp-late')
  })

  it('reset removes an already staged candidate and all linked receipt state', async () => {
    vi.mocked(uploadReceiptFile).mockResolvedValue({
      id: 'temp-a', name: 'a.jpg', status: 'uploaded',
    })
    vi.mocked(recognizeReceiptFile).mockResolvedValue([recognized('temp-a')])
    const store = useExpenseStore()
    store.categories = CATEGORIES
    await store.addReceiptFiles([file('a.jpg')])

    expect(store.receiptFiles[0]?.candidate).toBeDefined()
    store.reset()

    expect(store.receiptFiles).toEqual([])
    expect(store.items).toEqual([])
  })
})
