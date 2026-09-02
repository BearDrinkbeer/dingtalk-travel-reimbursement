import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { calculateTotals, getExpenseCategories } from '@/api/expenses'
import { useExpenseStore } from '@/stores/expense'

const MANUAL_CATEGORIES = [
  { id: 'local_transport', name: '市内交通费', order: 1, manualSelectable: true },
  { id: 'rail_fare', name: '火车票', order: 2, manualSelectable: true },
]

vi.mock('@/api/expenses', () => ({
  calculateTotals: vi.fn(),
  getExpenseCategories: vi.fn(),
}))

describe('expense store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(calculateTotals).mockReset()
    vi.mocked(getExpenseCategories).mockReset()
  })

  it('keeps integer-cent totals exact and supports item CRUD', () => {
    const store = useExpenseStore()
    store.categories = MANUAL_CATEGORIES
    const firstId = store.upsertManualItem({
      category: 'local_transport',
      date: '2026-07-01',
      displayDate: '2026-07-01',
      description: '市内交通',
      amount: '44.89',
      receiptCount: 4,
    })
    store.upsertManualItem({
      category: 'rail_fare',
      date: '2026-06-30',
      displayDate: '2026-06-30',
      description: '北京南-合肥南',
      amount: '454',
      receiptCount: 1,
    })
    store.upsertManualItem({
      id: firstId,
      category: 'local_transport',
      date: '2026-07-01',
      displayDate: '2026-07-01',
      description: '修改后的市内交通',
      amount: '44.89',
      receiptCount: 5,
    })

    expect(store.items).toHaveLength(2)
    expect(store.displayExpenseTotal).toBe('498.89')
    expect(store.localReceiptCount).toBe(6)
    store.removeItem(firstId)
    expect(store.displayExpenseTotal).toBe('454.00')
    expect(store.localReceiptCount).toBe(1)
  })

  it('uses the server-provided technical item limit', () => {
    const store = useExpenseStore()
    store.categories = MANUAL_CATEGORIES
    store.setExpenseItemLimit(2)
    for (let index = 0; index < 2; index += 1) {
      store.upsertManualItem({
        category: 'local_transport',
        date: '2026-07-01',
        displayDate: '2026-07-01',
        description: `费用 ${index}`,
        amount: '1.00',
        receiptCount: 1,
      })
    }

    expect(store.maxExpenseItems).toBe(2)
    expect(() => store.upsertManualItem({
      category: 'local_transport',
      date: '2026-07-01',
      displayDate: '2026-07-01',
      description: '超限费用',
      amount: '1.00',
      receiptCount: 1,
    })).toThrow('当前最多添加 2 条票据费用明细')
  })

  it('exposes items in stable occurrence-date order without changing entry order', () => {
    const store = useExpenseStore()
    store.categories = MANUAL_CATEGORIES
    const entered = [
      ['2026-07-08', '同日先录入'],
      ['2026-06-30', '最早发生'],
      ['2026-07-08', '同日后录入'],
      ['2026-07-07', '中间发生'],
    ] as const
    for (const [date, description] of entered) {
      store.upsertManualItem({
        category: 'rail_fare',
        date,
        displayDate: date,
        description,
        amount: '1.00',
        receiptCount: 1,
      })
    }

    expect(store.items.map((item) => item.description)).toEqual(entered.map((item) => item[1]))
    expect(store.sortedItems.map((item) => item.description)).toEqual([
      '最早发生',
      '中间发生',
      '同日先录入',
      '同日后录入',
    ])
  })

  it('requires explicit values for special trips but not automatic trips', () => {
    const store = useExpenseStore()
    store.categories = MANUAL_CATEGORIES
    store.setSubsidyIncluded(true)
    Object.assign(store.trip, {
      startDate: '2026-06-30',
      startTime: '09:00',
      endDate: '2026-07-07',
      endTime: '18:00',
    })
    expect(store.tripPayload()).toEqual({
      tripType: 'business',
      startDate: '2026-06-30',
      startTime: '09:00',
      endDate: '2026-07-07',
      endTime: '18:00',
    })

    store.setTripType('project')
    Object.assign(store.trip, {
      startDate: '2026-06-01',
      endDate: '2026-07-01',
    })
    expect(store.projectCalendarDays).toBe(31)
    expect(store.projectPolicyType).toBe('long_term_project')
    expect(store.requiresPolicyConfirmation).toBe(false)
    expect(store.tripPayload()).toMatchObject({
      tripType: 'project',
    })

    Object.assign(store.trip, {
      startDate: '2026-06-01',
      endDate: '2026-06-30',
    })
    expect(store.projectCalendarDays).toBe(30)
    expect(store.projectPolicyType).toBe('short_term_project')
    expect(store.requiresPolicyConfirmation).toBe(false)

    store.setTripType('internal')
    Object.assign(store.trip, {
      confirmedEffectiveDays: '366.5',
      policyConfirmed: true,
    })
    expect(store.tripPayload()).toBeNull()
    expect(store.policyInputError).toContain('0 到 366')
    Object.assign(store.trip, {
      confirmedEffectiveDays: '1.5',
      policyConfirmed: true,
      noSubsidyException: true,
    })
    expect(store.tripPayload()).toMatchObject({
      tripType: 'internal',
      confirmedEffectiveDays: '1.5',
      noSubsidyException: true,
    })
  })

  it('does not build a payload from non-exact time strings', () => {
    const store = useExpenseStore()
    store.setSubsidyIncluded(true)
    Object.assign(store.trip, {
      startDate: '2026-06-30',
      endDate: '2026-07-07',
      startTime: '9:00',
      endTime: '18:00',
    })
    expect(store.tripPayload()).toBeNull()
    store.trip.startTime = '09:00:00'
    expect(store.tripPayload()).toBeNull()
  })

  it('reconciles the summary with server values and reset removes current form data', async () => {
    vi.mocked(calculateTotals).mockResolvedValue({
      expenseTotal: '44.89',
      subsidyTotal: '800.00',
      totalAmount: '844.89',
      receiptCount: 4,
      uppercaseAmount: '捌佰肆拾肆元捌角玖分',
      subsidy: {
        tripType: 'business',
        calendarDays: 8,
        effectiveDays: '8.0',
        dailyRate: '100.00',
        total: '800.00',
      },
    })
    const store = useExpenseStore()
    store.categories = MANUAL_CATEGORIES
    store.setSubsidyIncluded(true)
    Object.assign(store.trip, {
      startDate: '2026-06-30',
      endDate: '2026-07-07',
    })
    store.manualProject = true
    store.manualProjectText = '临时项目'
    store.upsertManualItem({
      category: 'local_transport',
      date: '2026-07-01',
      displayDate: '2026-07-01',
      description: '市内交通',
      amount: '44.89',
      receiptCount: 4,
    })
    await store.refreshCalculations()
    expect(store.displayTotal).toBe('844.89')
    expect(store.displayReceiptCount).toBe(4)
    expect(store.totals?.uppercaseAmount).toBe('捌佰肆拾肆元捌角玖分')

    store.reset()
    expect(store.items).toEqual([])
    expect(store.includeSubsidy).toBe(false)
    expect(store.manualProjectText).toBe('')
    expect(store.trip.startDate).toBe('')
    expect(store.totals).toBeNull()
  })

  it('surfaces category loading failure and blocks manual items until retry succeeds', async () => {
    vi.mocked(getExpenseCategories).mockRejectedValueOnce(new Error('network failed'))
    const store = useExpenseStore()

    await expect(store.loadCategories()).resolves.toBe(false)
    expect(store.categoryLoadError).toContain('费用类别加载失败')
    expect(store.manualCategories).toEqual([])
    expect(() => store.upsertManualItem({
      category: 'local_transport',
      date: '2026-07-01',
      displayDate: '2026-07-01',
      description: '市内交通',
      amount: '44.89',
      receiptCount: 1,
    })).toThrow('费用类别不可用')

    vi.mocked(getExpenseCategories).mockResolvedValueOnce(MANUAL_CATEGORIES)
    await expect(store.loadCategories(true)).resolves.toBe(true)
    expect(store.categoryLoadError).toBe('')
    expect(store.manualCategories).toHaveLength(2)
  })

  it('builds an Excel payload without identity or client totals and blocks stale calculations', async () => {
    vi.mocked(calculateTotals).mockResolvedValue({
      expenseTotal: '44.89',
      subsidyTotal: '800.00',
      totalAmount: '844.89',
      receiptCount: 1,
      uppercaseAmount: '捌佰肆拾肆元捌角玖分',
      subsidy: {
        tripType: 'business',
        calendarDays: 8,
        effectiveDays: '8.0',
        dailyRate: '100.00',
        total: '800.00',
      },
    })
    const store = useExpenseStore()
    store.categories = MANUAL_CATEGORIES
    store.selectedProjectId = 7
    store.setSubsidyIncluded(true)
    Object.assign(store.trip, {
      startDate: '2026-06-30',
      endDate: '2026-07-07',
    })
    store.upsertManualItem({
      category: 'local_transport',
      date: '2026-07-01',
      displayDate: '2026-07-01',
      description: '市内交通',
      amount: '44.89',
      receiptCount: 1,
    })

    expect(store.excelDisabledReason).toContain('等待服务端')
    expect(store.buildExcelPayload()).toBeNull()
    await store.refreshCalculations()
    const payload = store.buildExcelPayload()
    expect(payload).toMatchObject({
      project: { mode: 'selected', id: 7 },
      trip: { startDate: '2026-06-30', endDate: '2026-07-07' },
      items: [{
        category: 'local_transport',
        date: '2026-07-01',
        amount: '44.89',
        receiptCount: 1,
      }],
    })
    expect(payload).not.toHaveProperty('employee')
    expect(payload).not.toHaveProperty('department')
    expect(payload).not.toHaveProperty('totals')
    expect(payload?.items[0]).not.toHaveProperty('id')
    expect(payload?.items[0]).not.toHaveProperty('source')

    store.trip.endDate = '2026-07-08'
    expect(store.calculationsCurrent).toBe(false)
    expect(store.buildExcelPayload()).toBeNull()
  })

  it('does not require a trip when subsidy is not selected', async () => {
    vi.mocked(calculateTotals).mockResolvedValue({
      expenseTotal: '0.00', subsidyTotal: '0.00', totalAmount: '0.00', receiptCount: 0,
      uppercaseAmount: '零元整', subsidy: null,
    })
    const store = useExpenseStore()
    expect(store.excelDisabledReason).toBe('请选择报销项目')
    store.manualProject = true
    expect(store.excelDisabledReason).toBe('请填写报销项目/预算代码')
    store.manualProjectText = '临时项目'
    expect(store.excelDisabledReason).toBe('费用类别尚未正确加载')
    store.categories = MANUAL_CATEGORIES
    expect(store.includeSubsidy).toBe(false)
    await store.refreshCalculations()
    expect(store.excelDisabledReason).toBe('')
    expect(store.buildExcelPayload()).toMatchObject({ trip: null, items: [] })

    store.setSubsidyIncluded(true)
    expect(store.excelDisabledReason).toContain('出发和返回')
  })
})
