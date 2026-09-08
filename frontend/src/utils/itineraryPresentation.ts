import type { ExpenseItem } from '@/types/expenses'
import { isItineraryOcrResult } from '@/types/receipts'
import type { ReimbursementDraftFile } from '@/types/reimbursements'
import type { ItinerarySuggestion } from '@/utils/itineraryMatching'
import { centsToMoney, moneyToCents } from '@/utils/money'

function amountLabel(amount: string | null, currency: string | null): string {
  const cents = moneyToCents(amount ?? '')
  if (cents === null) return '金额未识别'
  return currency === 'CNY' ? `¥${centsToMoney(cents)}` : `${centsToMoney(cents)} ${currency || '币种未识别'}`
}

export function itineraryOptionPresentation(
  file: ReimbursementDraftFile,
  items: readonly ExpenseItem[] = [],
  suggestion?: ItinerarySuggestion,
): { label: string; summary: string; route: string; association: string; searchText: string; matchedTripSummary: string } {
  const result = file.ocrResult
  const recognized = isItineraryOcrResult(result) && result.fileId === file.id && result.status === 'recognized'
  const linked = items.filter((item) => item.itineraryFileIds?.includes(file.id))
  const association = linked.length ? `已关联 ${linked.length} 笔费用` : '尚未关联'
  if (!recognized) {
    return { label: file.name, summary: '行程信息待识别', route: '', association, searchText: file.name, matchedTripSummary: '' }
  }
  const dates = result.summary.startDate
    ? result.summary.endDate && result.summary.endDate !== result.summary.startDate
      ? `${result.summary.startDate} 至 ${result.summary.endDate}` : result.summary.startDate
    : '日期未识别'
  const total = amountLabel(result.summary.amount, result.summary.currency)
  const partial = !result.complete || result.processedPageCount !== result.pageCount || result.warnings.length || result.error
    ? ' · 信息不完整，需核对' : ''
  const tripCount = result.trips.length ? `${result.trips.length} 次行程` : '行程明细未识别'
  const summary = `合计 ${total} · ${dates} · ${tripCount}${partial}`
  const matchingTrip = suggestion?.itineraryFileId === file.id ? suggestion.trip : undefined
  const routes = result.trips.map((trip) => `${trip.origin || '起点未识别'} → ${trip.destination || '终点未识别'}`)
  const route = matchingTrip ? `${matchingTrip.origin} → ${matchingTrip.destination}`
    : result.trips.length > 1 ? `多行程：${routes.join('；')}` : routes[0] || '路线未识别'
  const matchedTripSummary = matchingTrip
    ? `${suggestion?.basis === 'amount_only' ? '同金额候选（需核对）' : '推荐行程'} ${amountLabel(matchingTrip.amount, result.summary.currency)} · ${matchingTrip.date || '日期未识别'}` : ''
  const label = `${file.name} · ${total} · ${tripCount}`
  const searchText = [file.name, summary, ...routes, ...result.trips.flatMap((trip) => [trip.amount, trip.date]), association].join(' ')
  return { label, summary, route, association, searchText, matchedTripSummary }
}
