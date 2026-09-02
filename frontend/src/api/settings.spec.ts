import { beforeEach, describe, expect, it, vi } from 'vitest'

import { http } from '@/api/http'
import { normalizeSubsidyRate, updateExpenseSettings } from '@/api/settings'

vi.mock('@/api/http', () => ({
  http: { put: vi.fn() },
}))

describe('settings API', () => {
  beforeEach(() => {
    vi.mocked(http.put).mockReset()
  })

  it('normalizes the decimal string before sending it', async () => {
    const rates = {
      business: '88.5',
      short_term_project: '100',
      long_term_project: '150',
      same_city_project: '50',
      internal: '100',
    } as const
    vi.mocked(http.put).mockResolvedValue({
      data: { data: { subsidyRates: rates, calculationMode: 'half_day_12' } },
    })

    await updateExpenseSettings({ subsidyRates: rates, calculationMode: 'half_day_12' })

    expect(http.put).toHaveBeenCalledWith('/admin/settings', {
      subsidyRates: {
        business: '88.50',
        short_term_project: '100.00',
        long_term_project: '150.00',
        same_city_project: '50.00',
        internal: '100.00',
      },
      calculationMode: 'half_day_12',
    })
  })

  it('rejects zero, excessive rates, negative values and excess precision locally', async () => {
    for (const value of ['0', '10000.01', '-1', '88.501']) {
      expect(normalizeSubsidyRate(value)).toBeNull()
      await expect(
        updateExpenseSettings({
          subsidyRates: {
            business: value,
            short_term_project: '100.00',
            long_term_project: '150.00',
            same_city_project: '50.00',
            internal: '100.00',
          },
          calculationMode: 'half_day_12',
        }),
      ).rejects.toThrow('各出差类型')
    }
    expect(http.put).toHaveBeenCalledTimes(0)
  })
})
