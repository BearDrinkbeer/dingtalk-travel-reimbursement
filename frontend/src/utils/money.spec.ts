import { describe, expect, it } from 'vitest'

import { centsToMoney, moneyToCents } from '@/utils/money'

describe('money helpers', () => {
  it('uses integer cents without binary floating point aggregation', () => {
    const values = ['44.89', '454', '473.50'].map(moneyToCents)
    expect(values).toEqual([4489, 45400, 47350])
    expect(centsToMoney(values.reduce<number>((sum, value) => sum + (value ?? 0), 0))).toBe(
      '972.39',
    )
  })

  it('rejects invalid precision and negative values', () => {
    expect(moneyToCents('44.899')).toBeNull()
    expect(moneyToCents('-1')).toBeNull()
    expect(moneyToCents('not-money')).toBeNull()
  })
})
