import type {
  ExcelExpenseItemInput,
  ExcelGeneratePayload,
  ExpenseTotals,
  ExpenseItem,
  ExcelProjectInput,
  TripInput,
} from '@/types/expenses'
import type { OcrReceiptCandidate } from '@/types/receipts'
import type { OaFormOption } from '@/types/oa'

export type ReimbursementDraftStatus =
  | 'DRAFT'
  | 'REVIEW_READY'
  | 'LOCKED'
  | 'EXPIRED'

export interface ReimbursementDraftExpenseItemInput extends Omit<ExcelExpenseItemInput, 'date' | 'amount'> {
  date: string | null
  amount: string | null
  sourceFileId?: string
  itineraryFileIds?: string[]
  requiresItinerary?: boolean
  transportType?: ExpenseItem['transportType']
  originalCurrency?: string
  originalAmount?: string
  cnyAmountConfirmed?: boolean
  requiresCnyConfirmation?: boolean
}

export interface ReimbursementDraftInput extends Omit<ExcelGeneratePayload, 'items' | 'project'> {
  project?: ExcelProjectInput | null
  editingState?: { includeSubsidy: boolean; trip: TripInput }
  ocrDispositionVersion: 0 | 1
  companyValue: string
  budgetCodeValue: string
  items: ReimbursementDraftExpenseItemInput[]
  dismissedOcrFileIds: string[]
}

export interface ReimbursementDraftDepartment {
  id: string
  name: string
}

export interface ReimbursementDraftTemplateBinding {
  processCode: string
  configVersion: number
  schemaFingerprint: string
}

export interface ReimbursementDraftSummary {
  id: string
  status: ReimbursementDraftStatus
  revision: number
  department: ReimbursementDraftDepartment
  templateConfigVersion: number
  relatedApprovalCount: number
  expiresAt: string
  createdAt: string
  updatedAt: string
  lockedAt: string | null
}

export interface ReimbursementRelatedApprovalQueryWindow {
  startTimeMs: number
  endTimeMs: number
}

export interface ReimbursementRelatedApproval {
  processInstanceId: string
  profileKey: string
  sourceProcessCode: string
  title: string
  businessId: string
  startDate: string
  endDate: string
  queryWindow: ReimbursementRelatedApprovalQueryWindow
  verifiedAt: string
}

export interface ReimbursementRelatedApprovalSummary {
  count: number
  startDate: string
  endDate: string
}

export interface ReimbursementDraft extends ReimbursementDraftSummary {
  template: ReimbursementDraftTemplateBinding
  input: ReimbursementDraftInput
  totals: ExpenseTotals
  relatedApprovals: ReimbursementRelatedApproval[]
  relatedApprovalSummary: ReimbursementRelatedApprovalSummary | null
}

export interface ReimbursementDraftList {
  items: ReimbursementDraftSummary[]
  offset: number
  limit: number
  total: number
}

export interface OaReimbursementTravelProfile {
  profileKey: string
  displayName: string
  processCode: string
  schemaFingerprint: string
  travelTypeOption: OaFormOption
}

export interface OaReimbursementOptions {
  templateConfigVersion: number
  reimbursementProcessCode: string
  companyOptions: OaFormOption[]
  budgetCodeOptions: OaFormOption[]
  travelProfiles: OaReimbursementTravelProfile[]
}

export interface OaTravelApprovalQueryWindow {
  from: string
  to: string
}

export interface OaTravelApproval {
  processInstanceId: string
  profileKey: string
  profileDisplayName: string
  sourceProcessCode: string
  travelTypeOption: OaFormOption
  title: string
  businessId: string
  startDate: string
  endDate: string
  createdAt: string
  finishedAt: string | null
}

export interface OaTravelApprovalList {
  templateConfigVersion: number
  queryWindow: OaTravelApprovalQueryWindow
  items: OaTravelApproval[]
}

export interface ReimbursementRelatedApprovalSelection {
  processInstanceId: string
  profileKey: string
  queryWindow: {
    from: string
    to: string
  }
}

export type ReimbursementDraftFileRole =
  | 'EXPENSE_SOURCE'
  | 'ATTACHMENT_ONLY'

export type ReimbursementDraftFileStatus =
  | 'RESERVED'
  | 'WRITING'
  | 'ACTIVE'
  | 'FAILED'
  | 'DELETING'
  | 'PURGED'

export type ReimbursementDraftFileOcrStatus =
  | 'NOT_REQUESTED'
  | 'RUNNING'
  | 'COMPLETE'
  | 'FAILED'

export interface ReimbursementDraftFile {
  id: string
  name: string
  role: ReimbursementDraftFileRole
  sortOrder: number
  status: ReimbursementDraftFileStatus
  mediaType: string
  sizeBytes: number
  ocrStatus: ReimbursementDraftFileOcrStatus
  ocrResult: OcrReceiptCandidate | null
}

export interface ReimbursementDraftFileList {
  draftId: string
  revision: number
  items: ReimbursementDraftFile[]
}

export interface ReimbursementDraftFileMutation {
  draftId: string
  revision: number
  file: ReimbursementDraftFile
}

export interface ReimbursementDraftFileDeletion {
  draftId: string
  revision: number
  deletedFileId: string
}

export interface ReimbursementDraftDeletion {
  deletedDraftId: string
}

export interface ReimbursementDraftFileUpdate {
  expectedRevision: number
  role?: ReimbursementDraftFileRole
  name?: string
}

export interface ReimbursementDraftOcrInput {
  expectedRevision: number
  tripYear?: number
}

export interface ReimbursementExcelPreview {
  blob: Blob
  filename: string
}

export type ReimbursementSubmissionStatus =
  | 'QUEUED'
  | 'VALIDATING'
  | 'GENERATING_EXCEL'
  | 'UPLOADING'
  | 'OA_CREATING'
  | 'VERIFYING'
  | 'FAILED_RETRYABLE'
  | 'RECONCILING'
  | 'ORPHAN_CLEANUP'
  | 'SUBMITTED'
  | 'FAILED_FINAL'
  | 'MANUAL_REVIEW'

export interface ReimbursementSubmissionError {
  code: string
  message: string
}

export interface ReimbursementSubmission {
  submissionId: string
  draftId: string
  status: ReimbursementSubmissionStatus
  statusVersion: number
  attemptCount: number
  processInstanceId: string | null
  businessId: string | null
  approvalUrl: string | null
  error: ReimbursementSubmissionError | null
  pollAfterMs: number
  createdAt: string
  updatedAt: string
  submittedAt: string | null
}

export interface SubmitReimbursementInput {
  expectedRevision: number
}
