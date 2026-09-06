export type ReceiptFileStatus =
  | 'queued'
  | 'uploading'
  | 'uploaded'
  | 'recognizing'
  | 'recognized'
  | 'done'
  | 'failed'

export interface ReceiptFileState {
  localId: string
  file: File
  tempId?: string
  name: string
  size: number
  uploadProgress: number
  status: ReceiptFileStatus
  error?: string
  ocrItemId?: string
  candidate?: OcrReceiptCandidateDraft
}

export interface UploadedReceipt {
  id: string
  name: string
  status: 'uploaded'
}

export interface OcrReceiptError {
  code: string
  message: string
}

export interface OcrReceiptCandidate {
  fileId: string
  type: string
  categoryId: string
  categoryName: string
  date: string | null
  description: string | null
  amount: string | null
  transportType?: 'ride_hailing' | 'taxi' | 'rail' | 'hotel' | 'other'
  requiresItinerary?: boolean
  originalCurrency?: string | null
  originalAmount?: string | null
  receiptCount: 1
  source: 'ocr'
  confidence: string
  warnings: string[]
  status: 'recognized' | 'failed'
  error: OcrReceiptError | null
}

export interface OcrReceiptCandidateDraft
  extends Omit<OcrReceiptCandidate, 'date' | 'description' | 'amount'> {
  date: string
  description: string
  amount: string
}
