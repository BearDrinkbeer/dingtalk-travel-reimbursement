import { isTaxiExpense, type ExpenseItem } from '@/types/expenses'
import { isItineraryOcrResult, type ItineraryOcrResult } from '@/types/receipts'
import { receiptOcrResult, type ReimbursementDraftFile } from '@/types/reimbursements'
import { isActiveProof } from '@/utils/expenseProofs'
import { moneyToCents } from '@/utils/money'

interface NumberedEvidence { invoiceNumbers?: string[]; orderNumbers?: string[] }
interface SourceInvoice { item: ExpenseItem; tokens: Set<string> }
interface ItineraryEvidence { fileId: string; result: ItineraryOcrResult; tokens: Set<string> }
export interface ItineraryMatch {
  sourceFileId: string
  itineraryFileId: string
  basis: 'identifier' | 'amount_date_route'
}

function identifiers(evidence: NumberedEvidence): Set<string> {
  const tokens = new Set<string>()
  for (const [kind, numbers] of [['invoice', evidence.invoiceNumbers], ['order', evidence.orderNumbers]] as const) {
    for (const number of numbers ?? []) {
      const normalized = number.trim().toUpperCase()
      if (normalized) tokens.add(`${kind}:${normalized}`)
    }
  }
  return tokens
}

function conflictingIdentifiers(left: Set<string>, right: Set<string>): boolean {
  return ['invoice:', 'order:'].some((prefix) => {
    const leftNumbers = [...left].filter((token) => token.startsWith(prefix))
    const rightNumbers = [...right].filter((token) => token.startsWith(prefix))
    return leftNumbers.length > 0 && rightNumbers.length > 0
      && !leftNumbers.some((token) => right.has(token))
  })
}

function matchingTrip(invoice: SourceInvoice, trip: ItineraryOcrResult['trips'][number]): boolean {
  const amount = moneyToCents(invoice.item.amount)
  if (amount === null || amount <= 0 || moneyToCents(trip.amount ?? '') !== amount
    || !invoice.item.date || invoice.item.date !== trip.date) return false
  const normalize = (value: string) => value.replace(/[\s\p{P}]/gu, '').toUpperCase()
  const origin = normalize(trip.origin ?? '')
  const destination = normalize(trip.destination ?? '')
  const description = normalize(invoice.item.description)
  if (origin.length < 2 || destination.length < 2
    || origin.includes(destination) || destination.includes(origin)) return false
  const start = description.indexOf(origin)
  const end = description.indexOf(destination)
  return start >= 0 && end >= start + origin.length
    && start === description.lastIndexOf(origin) && end === description.lastIndexOf(destination)
}

export function matchItineraries(items: readonly ExpenseItem[], files: readonly ReimbursementDraftFile[]): ItineraryMatch[] {
  const sources: SourceInvoice[] = items.flatMap((item) => {
    const file = files.find((candidate) => candidate.id === item.sourceFileId)
    const evidence = receiptOcrResult(file)
    return item.sourceFileId && isTaxiExpense(item) && file?.role === 'EXPENSE_SOURCE'
      && file.status === 'ACTIVE' && evidence?.status === 'recognized' && evidence.fileId === file.id
      ? [{ item, tokens: identifiers(evidence) }] : []
  })
  const itineraries: ItineraryEvidence[] = files.flatMap((file) => {
    const result = file.ocrResult
    if (!isActiveProof(file, 'itinerary') || !isItineraryOcrResult(result)
      || result.fileId !== file.id || result.status !== 'recognized' || !result.complete
      || result.processedPageCount !== result.pageCount || result.warnings.length || result.error) return []
    const tokens = identifiers(result.summary)
    for (const trip of result.trips) for (const token of identifiers(trip)) tokens.add(token)
    return [{ fileId: file.id, result, tokens }]
  })
  const matches: ItineraryMatch[] = []
  for (const source of sources) {
    if (source.item.itineraryAutoMatchDisabled || source.item.itineraryFileIds?.length) continue
    const compatible = itineraries.filter((proof) => !conflictingIdentifiers(source.tokens, proof.tokens))
    const numbered = compatible.filter((proof) => [...source.tokens].some((token) => proof.tokens.has(token)))
    if (numbered.length) {
      const proof = numbered[0]!
      if (numbered.length === 1 && [...source.tokens].some((token) => proof.tokens.has(token)
        && sources.filter((candidate) => candidate.tokens.has(token)).length === 1)) {
        matches.push({ sourceFileId: source.item.sourceFileId!, itineraryFileId: proof.fileId, basis: 'identifier' })
      }
      continue
    }
    const routed = compatible.filter((proof) => proof.result.summary.currency === 'CNY'
      && proof.result.trips.some((trip) => matchingTrip(source, trip)))
    if (routed.length !== 1) continue
    const proof = routed[0]!
    const unique = proof.result.trips.some((trip) => matchingTrip(source, trip)
      && sources.filter((candidate) => matchingTrip(candidate, trip)).length === 1)
    if (unique) matches.push({ sourceFileId: source.item.sourceFileId!, itineraryFileId: proof.fileId, basis: 'amount_date_route' })
  }
  return matches
}
