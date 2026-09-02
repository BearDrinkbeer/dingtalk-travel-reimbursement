import { http } from './http'
import type { ApiEnvelope } from '@/types/auth'
import type { SubsidyRateType } from '@/types/expenses'
import { centsToMoney, moneyToCents } from '@/utils/money'

export const MAX_DAILY_SUBSIDY_CENTS = 1_000_000

export type SubsidyRates = Record<SubsidyRateType, string>

export interface ExpenseSettings {
  subsidyRates: SubsidyRates
  calculationMode: 'half_day_12'
}

export async function getExpenseSettings(): Promise<ExpenseSettings> {
  const response = await http.get<ApiEnvelope<ExpenseSettings>>('/settings')
  return response.data.data
}

export function normalizeSubsidyRate(value: string): string | null {
  const cents = moneyToCents(value)
  if (cents === null || cents <= 0 || cents > MAX_DAILY_SUBSIDY_CENTS) return null
  return centsToMoney(cents)
}

export async function updateExpenseSettings(
  input: ExpenseSettings,
): Promise<ExpenseSettings> {
  const normalizedRates = Object.fromEntries(
    Object.entries(input.subsidyRates).map(([tripType, value]) => {
      const normalized = normalizeSubsidyRate(value)
      if (normalized === null) {
        throw new Error('各出差类型的每日补助标准必须大于 0 且不超过 10000 元，最多两位小数')
      }
      return [tripType, normalized]
    }),
  ) as SubsidyRates
  const response = await http.put<ApiEnvelope<ExpenseSettings>>('/admin/settings', {
    ...input,
    subsidyRates: normalizedRates,
  })
  return response.data.data
}
