import { describe, expect, it } from 'vitest'

import { missingExpenseMaterials, requiresPaymentProof } from './expenseProofs'
import type { ExpenseItem } from '@/types/expenses'
import type { ReimbursementDraftFile } from '@/types/reimbursements'

describe('payment proof requirement', () => {
  it.each([
    ['500.00', 'hotel', 'unknown', false], ['500.01', 'hotel', 'unknown', true],
    ['600.00', 'rail_fare', 'high_speed', false], ['600.00', 'rail_fare', 'regular', true],
    ['600.00', 'rail_fare', 'emu', true], ['600.00', 'hotel', 'high_speed', true],
    ['', 'hotel', 'unknown', false],
  ] as const)('requires proof for %s %s %s: %s', (amount, category, railType, expected) => {
    expect(requiresPaymentProof({ amount, category, railType })).toBe(expected)
  })
})

describe('hotel stay details', () => {
  const bill = { id: 'hotel-1', status: 'ACTIVE', role: 'ATTACHMENT_ONLY', attachmentKind: 'hotel_bill' } as ReimbursementDraftFile
  const expense = (amount: string) => ({ category: 'lodging', amount, hotelBillFileIds: ['hotel-1'] }) as ExpenseItem
  it('requires details at any amount and independent payment proof above 500', () => {
    expect(missingExpenseMaterials(expense('1.00'), [])).toEqual(['住宿明细'])
    expect(missingExpenseMaterials(expense('500.00'), [bill])).toEqual([])
    expect(missingExpenseMaterials(expense('500.01'), [bill])).toEqual(['付款凭证'])
  })
  it('rejects deleted, wrong-purpose and unconfirmed files', () => {
    for (const file of [
      { ...bill, status: 'PURGED' }, { ...bill, attachmentKind: 'payment_proof' },
      { ...bill, materialClassification: { status: 'needs_confirmation' } },
    ]) expect(missingExpenseMaterials(expense('20.00'), [file as ReimbursementDraftFile])).toContain('住宿明细')
  })
  it('allows manually confirmed details despite failed OCR and sharing', () => {
    const manual = { ...bill, ocrStatus: 'FAILED', materialClassification: { status: 'confirmed' } } as ReimbursementDraftFile
    expect(missingExpenseMaterials(expense('20.00'), [manual])).toEqual([])
    expect(missingExpenseMaterials(expense('40.00'), [manual])).toEqual([])
  })
})
