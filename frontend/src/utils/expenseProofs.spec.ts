import { describe, expect, it } from 'vitest'

import { requiresPaymentProof } from './expenseProofs'

describe('payment proof requirement', () => {
  it.each([
    ['500.00', 'hotel', 'unknown', false], ['500.01', 'hotel', 'unknown', true],
    ['600.00', 'rail_fare', 'high_speed', false], ['600.00', 'rail_fare', 'regular', true],
    ['600.00', 'rail_fare', 'emu', true], ['600.00', 'hotel', 'high_speed', true],
    ['', 'hotel', 'unknown', false],
  ] as const)('requires proof for %s %s %s: %s', (amount, category, railType, expected) => {
    expect(requiresPaymentProof({ amount, category, railType })).toBe(expected)
  })
})
