import type { ExpenseItem, RailType } from '@/types/expenses'
import type { OcrReceiptCandidate } from '@/types/receipts'
import type { ReimbursementAttachmentKind, ReimbursementDraftFile } from '@/types/reimbursements'
import { moneyToCents } from '@/utils/money'

export function requiresPaymentProof(item: Pick<ExpenseItem, 'amount' | 'category' | 'railType'>): boolean {
  return (moneyToCents(item.amount) ?? 0) > 50_000
    && !(item.category === 'rail_fare' && item.railType === 'high_speed')
}

export function isActiveProof(file: ReimbursementDraftFile, kind: ReimbursementAttachmentKind): boolean {
  return file.status === 'ACTIVE' && file.role === 'ATTACHMENT_ONLY' && file.attachmentKind === kind
}

export function evidenceRailType(category: string, selected: RailType | undefined, evidence: OcrReceiptCandidate | null): RailType {
  if (category !== 'rail_fare' || hasKnownNonRailEvidence(evidence)) return 'unknown'
  return evidence?.railType && evidence.railType !== 'unknown' ? evidence.railType : selected ?? 'unknown'
}

export function hasKnownNonRailEvidence(evidence: OcrReceiptCandidate | null): boolean {
  return Boolean(evidence?.categoryId && !['rail_fare', 'other'].includes(evidence.categoryId))
}
