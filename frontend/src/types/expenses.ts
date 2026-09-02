// Category identifiers are an opaque server-owned contract. The category API
// supplies both the identifiers and the options that may be selected manually.
export type ExpenseCategoryId = string

export interface ExpenseCategoryMetadata {
  id: ExpenseCategoryId
  name: string
  order: number
  manualSelectable: boolean
}

export type TripType =
  | 'business'
  | 'project'
  | 'same_city_project'
  | 'internal'

export type SubsidyRateType =
  | 'business'
  | 'short_term_project'
  | 'long_term_project'
  | 'same_city_project'
  | 'internal'

export interface TripInput {
  tripType: TripType
  startDate: string
  startTime: string
  endDate: string
  endTime: string
  policyConfirmed?: boolean
  confirmedEffectiveDays?: string
  noSubsidyException?: boolean
}

export interface ExpenseItem {
  id: string
  category: ExpenseCategoryId
  date?: string
  displayDate: string
  description: string
  amount: string
  receiptCount: number
  source: 'ocr' | 'manual' | 'system'
  confidence?: string
  warnings?: string[]
}

export interface SubsidyResult {
  tripType: SubsidyRateType
  calendarDays: number
  effectiveDays: string
  dailyRate: string
  total: string
}

export interface ExpenseTotals {
  expenseTotal: string
  subsidyTotal: string
  totalAmount: string
  receiptCount: number
  uppercaseAmount: string
  subsidy: SubsidyResult | null
}

export type ExcelProjectInput =
  | { mode: 'selected'; id: number }
  | { mode: 'manual'; text: string }

export interface ExcelExpenseItemInput {
  category: ExpenseCategoryId
  date: string
  displayDate: string
  description: string
  amount: string
  receiptCount: number
}

export interface ExcelGeneratePayload {
  project: ExcelProjectInput
  trip: TripInput | null
  items: ExcelExpenseItemInput[]
}
