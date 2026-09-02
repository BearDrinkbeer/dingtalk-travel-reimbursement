import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { logout as logoutRequest } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { useExpenseStore } from '@/stores/expense'

const callbacks = vi.hoisted(() => ({ unauthorized: null as (() => void) | null }))

vi.mock('@/api/http', () => ({
  setCsrfToken: vi.fn(),
  setUnauthorizedHandler: vi.fn((handler: () => void) => {
    callbacks.unauthorized = handler
  }),
}))

vi.mock('@/api/auth', () => ({
  getMe: vi.fn(),
  getPublicConfig: vi.fn(),
  loginWithDingTalk: vi.fn(),
  loginWithMock: vi.fn(),
  logout: vi.fn().mockResolvedValue(undefined),
  selectDepartment: vi.fn(),
}))

vi.mock('@/api/expenses', () => ({
  calculateTotals: vi.fn(),
  getExpenseCategories: vi.fn(),
}))

vi.mock('@/api/receipts', () => ({
  deleteReceiptFile: vi.fn(),
  recognizeReceiptFile: vi.fn(),
  uploadReceiptFile: vi.fn(),
}))

function seedReceiptMemory(): ReturnType<typeof useExpenseStore> {
  const expense = useExpenseStore()
  expense.receiptFiles.push({
    localId: 'local-a',
    tempId: 'temp-a',
    file: new File(['receipt'], 'a.jpg', { type: 'image/jpeg' }),
    name: 'a.jpg',
    size: 7,
    uploadProgress: 100,
    status: 'done',
    ocrItemId: 'ocr-temp-a',
  })
  expense.items.push({
    id: 'ocr-temp-a',
    category: 'other',
    date: '2026-06-30',
    displayDate: '2026-06-30',
    description: '票据',
    amount: '1.00',
    receiptCount: 1,
    source: 'ocr',
  })
  return expense
}

describe('authentication clears receipt memory', () => {
  beforeEach(() => {
    callbacks.unauthorized = null
    setActivePinia(createPinia())
    vi.mocked(logoutRequest).mockClear()
  })

  it('clears files and OCR candidates when the HTTP client reports 401', () => {
    useAuthStore()
    const expense = seedReceiptMemory()

    callbacks.unauthorized?.()

    expect(expense.receiptFiles).toEqual([])
    expect(expense.items).toEqual([])
  })

  it('clears files and OCR candidates after logout', async () => {
    const auth = useAuthStore()
    const expense = seedReceiptMemory()

    await auth.logout()

    expect(logoutRequest).toHaveBeenCalledOnce()
    expect(expense.receiptFiles).toEqual([])
    expect(expense.items).toEqual([])
  })
})
