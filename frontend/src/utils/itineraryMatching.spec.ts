import { describe, expect, it } from 'vitest'

import type { ExpenseItem } from '@/types/expenses'
import type { ItineraryOcrResult, OcrReceiptCandidate } from '@/types/receipts'
import type { ReimbursementDraftFile } from '@/types/reimbursements'
import { matchItineraries } from './itineraryMatching'

function invoice(id: string, changes: Partial<OcrReceiptCandidate> = {}): ReimbursementDraftFile {
  return {
    id, name: `${id}.pdf`, role: 'EXPENSE_SOURCE', attachmentKind: 'other', status: 'ACTIVE',
    sortOrder: 0, mediaType: 'application/pdf', sizeBytes: 10, ocrStatus: 'COMPLETE',
    ocrResult: {
      fileId: id, type: 'ride_hailing', categoryId: 'local_transport', categoryName: '市内交通',
      date: '2026-09-01', description: '合肥机场至滨湖酒店', amount: '60.00', transportType: 'ride_hailing',
      receiptCount: 1, source: 'ocr', confidence: '0.95', warnings: [], status: 'recognized', error: null,
      invoiceNumbers: [], orderNumbers: [], ...changes,
    },
  }
}
function item(sourceFileId: string, changes: Partial<ExpenseItem> = {}): ExpenseItem {
  return {
    id: `ocr-${sourceFileId}`, sourceFileId, source: 'ocr', category: 'local_transport',
    date: '2026-09-01', displayDate: '2026-09-01', description: '合肥机场至滨湖酒店', amount: '60.00',
    receiptCount: 1, transportType: 'ride_hailing', itineraryFileIds: [], ...changes,
  }
}
function itinerary(id: string, changes: Partial<ItineraryOcrResult> = {}): ReimbursementDraftFile {
  return {
    id, name: `${id}.pdf`, role: 'ATTACHMENT_ONLY', attachmentKind: 'itinerary', status: 'ACTIVE',
    sortOrder: 1, mediaType: 'application/pdf', sizeBytes: 10, ocrStatus: 'COMPLETE',
    ocrResult: {
      fileId: id, version: 1, kind: 'itinerary', status: 'recognized', source: 'pdf_text', pageCount: 1,
      processedPageCount: 1, complete: true, warnings: [], error: null,
      summary: { currency: 'CNY', amount: '60.00', startDate: '2026-09-01', endDate: '2026-09-01', invoiceNumbers: ['INV-001'], orderNumbers: [] },
      trips: [{ page: 1, row: 1, date: '2026-09-01', amount: '60.00', origin: '合肥机场', destination: '滨湖酒店', invoiceNumbers: [], orderNumbers: [] }],
      ...changes,
    },
  }
}

describe('conservative itinerary matching', () => {
  it('uses an explicit unique invoice number before route/amount evidence', () => {
    const files = [invoice('invoice', { invoiceNumbers: [' inv-001 '], amount: '99.00' }), itinerary('proof')]
    expect(matchItineraries([item('invoice', { amount: '99.00', description: '打车' })], files))
      .toEqual([{ sourceFileId: 'invoice', itineraryFileId: 'proof', basis: 'identifier' }])
  })

  it('matches by amount, occurrence date and both route endpoints only when unique', () => {
    const files = [invoice('invoice'), itinerary('proof')]
    expect(matchItineraries([item('invoice')], files)).toEqual([{ sourceFileId: 'invoice', itineraryFileId: 'proof', basis: 'amount_date_route' }])
    expect(matchItineraries([item('invoice', { description: '市内打车' })], files)).toEqual([])
    expect(matchItineraries([item('invoice', { date: '2026-09-02' })], files)).toEqual([])
  })

  it('rejects duplicate proof candidates and duplicate invoice candidates', () => {
    const source = invoice('invoice', { invoiceNumbers: ['INV-001'] })
    expect(matchItineraries([item('invoice')], [source, itinerary('proof-a'), itinerary('proof-b')])).toEqual([])
    expect(matchItineraries([item('invoice'), item('duplicate')], [source, invoice('duplicate', { invoiceNumbers: ['INV-001'] }), itinerary('proof')])).toEqual([])
    expect(matchItineraries([item('invoice'), item('duplicate')], [invoice('invoice'), invoice('duplicate'), itinerary('proof')])).toEqual([])
  })

  it('rejects reversed, overlapping or repeated endpoints even for one candidate', () => {
    const files = [invoice('invoice'), itinerary('proof')]
    expect(matchItineraries([item('invoice', { description: '滨湖酒店至合肥机场' })], files)).toEqual([])
    expect(matchItineraries([item('invoice', { description: '合肥机场至滨湖酒店再返回合肥机场' })], files)).toEqual([])
    const proof = itinerary('proof')
    const result = proof.ocrResult as ItineraryOcrResult
    result.trips[0]!.destination = '机场'
    expect(matchItineraries([item('invoice')], [files[0]!, proof])).toEqual([])
  })

  it('rejects conflicting comparable identifiers instead of falling back or accepting another shared identifier', () => {
    const proof = itinerary('proof')
    expect(matchItineraries([item('invoice')], [invoice('invoice', { invoiceNumbers: ['INV-002'] }), proof])).toEqual([])
    const result = proof.ocrResult as ItineraryOcrResult
    result.summary.orderNumbers = ['ORDER-001']
    expect(matchItineraries([item('invoice')], [invoice('invoice', { invoiceNumbers: ['INV-002'], orderNumbers: ['ORDER-001'] }), proof])).toEqual([])
    expect(matchItineraries([item('invoice')], [invoice('invoice', { invoiceNumbers: ['INV-001'], orderNumbers: ['ORDER-002'] }), proof])).toEqual([])
    expect(matchItineraries([item('invoice')], [invoice('invoice', { orderNumbers: ['ORDER-002'] }), proof])).toEqual([])
    expect(matchItineraries([item('invoice')], [invoice('invoice'), proof])).toHaveLength(1)
    expect(matchItineraries([item('invoice')], [invoice('invoice', { invoiceNumbers: ['INV-001'] }), proof]))
      .toEqual([{ sourceFileId: 'invoice', itineraryFileId: 'proof', basis: 'identifier' }])
  })

  it('never overwrites an existing link or matches non-taxi, partial, failed, foreign or warned evidence', () => {
    const source = invoice('invoice', { invoiceNumbers: ['INV-001'] })
    expect(matchItineraries([item('invoice', { itineraryFileIds: ['manual-proof'] })], [source, itinerary('proof')])).toEqual([])
    expect(matchItineraries([item('invoice', { itineraryAutoMatchDisabled: true })], [source, itinerary('proof')])).toEqual([])
    expect(matchItineraries([item('invoice', { itineraryAutoMatchDisabled: false })], [source, itinerary('proof')])).toHaveLength(1)
    expect(matchItineraries([item('invoice', { transportType: 'rail' })], [source, itinerary('proof')])).toEqual([])
    for (const changes of [{ complete: false }, { status: 'failed' as const }, { warnings: ['ITINERARY_TOTAL_CONFLICT'] }]) {
      expect(matchItineraries([item('invoice')], [source, itinerary('proof', changes)])).toEqual([])
    }
    const proof = itinerary('proof')
    proof.attachmentKind = 'payment_proof'
    expect(matchItineraries([item('invoice')], [source, proof])).toEqual([])
    const foreign = itinerary('proof')
    ;(foreign.ocrResult as ItineraryOcrResult).summary.currency = 'USD'
    expect(matchItineraries([item('invoice')], [invoice('invoice'), foreign])).toEqual([])
  })

  it('supports multiple expenses referring to distinct trips in one itinerary regardless of file order', () => {
    const first = invoice('invoice-a', { orderNumbers: ['ORDER-A'] })
    const second = invoice('invoice-b', { orderNumbers: ['ORDER-B'] })
    const proof = itinerary('proof')
    const result = proof.ocrResult as ItineraryOcrResult
    result.trips = [
      { ...result.trips[0]!, orderNumbers: ['ORDER-A'] },
      { ...result.trips[0]!, row: 2, orderNumbers: ['ORDER-B'] },
    ]
    const expected = [
      { sourceFileId: 'invoice-a', itineraryFileId: 'proof', basis: 'identifier' },
      { sourceFileId: 'invoice-b', itineraryFileId: 'proof', basis: 'identifier' },
    ]
    expect(matchItineraries([item('invoice-a'), item('invoice-b')], [first, second, proof])).toEqual(expected)
    expect(matchItineraries([item('invoice-a'), item('invoice-b')], [proof, first, second])).toEqual(expected)
  })
})
