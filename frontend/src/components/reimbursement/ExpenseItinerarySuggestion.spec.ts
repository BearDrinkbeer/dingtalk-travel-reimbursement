import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ExpenseItinerarySuggestion from './ExpenseItinerarySuggestion.vue'

describe('whole-document itinerary proposal', () => {
  it('shows trip count, total and range and requires a confirmation click', async () => {
    const wrapper = mount(ExpenseItinerarySuggestion, {
      props: { fileName: '199.pdf', disabled: false, previewLoading: false,
        suggestion: { sourceFileId: 'invoice', itineraryFileId: 'proof', basis: 'amount_only',
          transportType: 'ride_hailing', fill: {}, documentSummary: {
            amount: '199.00', tripCount: 15, startDate: '2026-08-25', endDate: '2026-09-04',
          } } },
      global: { stubs: { 'el-button': { template: '<button><slot /></button>' } } },
    })
    expect(wrapper.text()).toContain('15 笔 · ¥199.00')
    expect(wrapper.text()).toContain('2026-08-25 至 2026-09-04')
    expect(wrapper.text()).toContain('确认后仅关联文件，不改日期和说明')
    expect(wrapper.text()).not.toContain('→')
    expect(wrapper.emitted('confirm')).toBeUndefined()
    await wrapper.findAll('button').find(button => button.text() === '确认关联')!.trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
  })
})
