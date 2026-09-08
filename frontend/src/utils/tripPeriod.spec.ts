import { describe, expect, it } from 'vitest'

import { tripPeriodFromTime } from './tripPeriod'

describe('tripPeriodFromTime', () => {
  it.each([
    ['00:00', 'morning'],
    ['09:00', 'morning'],
    ['11:59', 'morning'],
    ['12:00', 'afternoon'],
    ['18:00', 'afternoon'],
    ['23:59', 'afternoon'],
  ])('maps %s to %s', (time, period) => {
    expect(tripPeriodFromTime(time)).toBe(period)
  })

  it.each(['', '9:00', '09:00:00', '24:00', '12:60', 'morning'])('does not infer a period from invalid input %s', (time) => {
    expect(tripPeriodFromTime(time)).toBeUndefined()
  })
})
