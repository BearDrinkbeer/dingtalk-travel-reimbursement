const MONEY_PATTERN = /^(0|[1-9]\d{0,11})(?:\.(\d{1,2}))?$/

export function moneyToCents(value: string): number | null {
  const normalized = value.trim()
  const match = MONEY_PATTERN.exec(normalized)
  if (!match) return null
  const whole = Number(match[1])
  const fraction = (match[2] ?? '').padEnd(2, '0')
  const cents = whole * 100 + Number(fraction)
  return Number.isSafeInteger(cents) ? cents : null
}

export function centsToMoney(value: number): string {
  if (!Number.isSafeInteger(value) || value < 0) return '0.00'
  return `${Math.floor(value / 100)}.${String(value % 100).padStart(2, '0')}`
}
