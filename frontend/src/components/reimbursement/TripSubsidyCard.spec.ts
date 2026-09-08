import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useExpenseStore } from '@/stores/expense'
import TripSubsidyCard from './TripSubsidyCard.vue'

enableAutoUnmount(afterEach)

describe('TripSubsidyCard half-day selection', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('calculates overseas subsidy from the administrator daily rate', () => {
    const expense = useExpenseStore()
    expense.setSubsidyIncluded(true)
    expense.setTripType('overseas')
    expense.totals = {
      expenseTotal: '0.00', subsidyTotal: '0.00', totalAmount: '0.00',
      receiptCount: 0, uppercaseAmount: '零元整',
      subsidy: { tripType: 'overseas', calendarDays: 3, effectiveDays: '3.0', dailyRate: '0.00', total: '0.00' },
    }
    const wrapper = mountCard()
    expect(wrapper.find('input[aria-label="境外出差补助总额（元）"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('自动计算规则')
    expect(wrapper.text()).toContain('有效 3.0 天')
    expect(wrapper.text()).toContain('¥0.00 / 天')
    expect(wrapper.findAllComponents({ name: 'ElCheckbox' })).toHaveLength(0)
  })

  function mountCard(readonly = false) {
    return mount(TripSubsidyCard, { props: { readonly }, global: { plugins: [ElementPlus] } })
  }

  it('locks all subsidy controls after the reimbursement is submitted', async () => {
    const expense = useExpenseStore()
    expense.setSubsidyIncluded(true)
    const wrapper = mountCard(true)

    expect(wrapper.findComponent({ name: 'ElForm' }).props('disabled')).toBe(true)
    expect(wrapper.get<HTMLInputElement>('[role="switch"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[role="switch"]').trigger('click')
    expect(expense.includeSubsidy).toBe(true)
  })

  it('selects morning or afternoon directly without exact-time inputs', async () => {
    const expense = useExpenseStore()
    expense.setSubsidyIncluded(true)
    const wrapper = mountCard()
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(4)
    expect(wrapper.findComponent({ name: 'ElTimePicker' }).exists()).toBe(false)
    const departure = wrapper.get('[aria-label="出发时段"]')
    const returned = wrapper.get('[aria-label="返回时段"]')
    expect(departure.get<HTMLInputElement>('input[value="morning"]').element.checked).toBe(true)
    expect(returned.get<HTMLInputElement>('input[value="afternoon"]').element.checked).toBe(true)

    await departure.get('input[value="afternoon"]').setValue()
    await returned.get('input[value="morning"]').setValue()
    expect(expense.trip.startTime).toBe('18:00')
    expect(expense.trip.endTime).toBe('09:00')
    expect(wrapper.text()).toContain('同一时段往返计 0.5 天')
  })

  it('displays legacy exact times using the noon boundary without mutating them', () => {
    const expense = useExpenseStore()
    expense.setSubsidyIncluded(true)
    expense.trip.startTime = '11:59'
    expense.trip.endTime = '12:00'
    const wrapper = mountCard()
    expect(wrapper.get<HTMLInputElement>('[aria-label="出发时段"] input[value="morning"]').element.checked).toBe(true)
    expect(wrapper.get<HTMLInputElement>('[aria-label="返回时段"] input[value="afternoon"]').element.checked).toBe(true)
    expect(expense.trip.startTime).toBe('11:59')
    expect(expense.trip.endTime).toBe('12:00')
  })

  it('hides period selection when subsidy is not requested', async () => {
    const expense = useExpenseStore()
    const wrapper = mountCard()
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0)
    await wrapper.get('[role="switch"]').trigger('click')
    expect(expense.includeSubsidy).toBe(true)
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(4)
    await wrapper.get('[role="switch"]').trigger('click')
    expect(expense.tripPayload()).toBeNull()
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0)
  })
})
