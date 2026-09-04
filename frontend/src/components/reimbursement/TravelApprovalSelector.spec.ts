import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'
import type {
  OaTravelApproval,
  ReimbursementRelatedApproval,
  ReimbursementRelatedApprovalSelection,
} from '@/types/reimbursements'
import TravelApprovalSelector from './TravelApprovalSelector.vue'

const candidate: OaTravelApproval = {
  processInstanceId: 'travel-1',
  profileKey: 'business',
  profileDisplayName: '境内出差',
  sourceProcessCode: 'PROC-TRAVEL',
  travelTypeOption: { value: 'business', label: '境内出差', key: null },
  title: '合肥出差申请',
  businessId: 'TRAVEL-1',
  startDate: '2026-09-01',
  endDate: '2026-09-03',
  createdAt: '2026-08-30T00:00:00Z',
  finishedAt: '2026-08-31T00:00:00Z',
}

const selection: ReimbursementRelatedApprovalSelection = {
  processInstanceId: 'travel-1',
  profileKey: 'business',
  queryWindow: { from: '2026-08-01', to: '2026-09-04' },
}

const linked: ReimbursementRelatedApproval = {
  processInstanceId: 'travel-1',
  profileKey: 'business',
  sourceProcessCode: 'PROC-TRAVEL',
  title: '合肥出差申请',
  businessId: 'TRAVEL-1',
  startDate: '2026-09-01',
  endDate: '2026-09-03',
  queryWindow: {
    startTimeMs: Date.parse('2026-08-01T00:00:00+08:00'),
    endTimeMs: Date.parse('2026-09-04T23:59:59.999+08:00'),
  },
  verifiedAt: '2026-09-04T00:00:00Z',
}

describe('TravelApprovalSelector', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.ResizeObserver = class ResizeObserver {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
  })

  it('emits the server profile and exact query window for a selected candidate', async () => {
    const drafts = useReimbursementDraftStore()
    drafts.travelApprovals = [candidate]
    drafts.travelApprovalQueryWindow = selection.queryWindow
    const wrapper = mount(TravelApprovalSelector, {
      props: { modelValue: [] },
      global: { plugins: [ElementPlus] },
    })

    wrapper.findComponent({ name: 'ElCheckbox' }).vm.$emit('change', true)
    await nextTick()

    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([[selection]])
    wrapper.unmount()
  })

  it('keeps a restored linked approval visible even when it is outside the latest search', () => {
    const drafts = useReimbursementDraftStore()
    drafts.travelApprovalsError = '暂不重新查询'
    const wrapper = mount(TravelApprovalSelector, {
      props: {
        modelValue: [selection],
        linkedApprovals: [linked],
        readonly: true,
      },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.text()).toContain('合肥出差申请')
    expect(wrapper.text()).toContain('已关联')
    expect(wrapper.text()).toContain('当前报销已进入提交阶段')
    expect(wrapper.findComponent({ name: 'ElCheckbox' }).props('disabled')).toBe(true)
    wrapper.unmount()
  })

  it('loads candidates by an explicit date range and keyword', async () => {
    const drafts = useReimbursementDraftStore()
    drafts.travelApprovals = [candidate]
    const load = vi.spyOn(drafts, 'loadTravelApprovals').mockResolvedValue(undefined)
    const wrapper = mount(TravelApprovalSelector, {
      props: { modelValue: [] },
      global: { plugins: [ElementPlus] },
    })
    const datePickers = wrapper.findAllComponents({ name: 'ElDatePicker' })
    const keyword = wrapper.findAllComponents({ name: 'ElInput' }).find(
      (item) => item.props('ariaLabel') === '搜索出差审批',
    )
    if (!datePickers[0] || !datePickers[1] || !keyword) throw new Error('Missing query inputs')
    datePickers[0].vm.$emit('update:modelValue', '2026-07-01')
    datePickers[1].vm.$emit('update:modelValue', '2026-09-04')
    keyword.vm.$emit('update:modelValue', '合肥')
    await nextTick()

    const query = wrapper.findAll('button').find((button) => button.text().trim() === '查询审批')
    if (!query) throw new Error('Missing query button')
    await query.trigger('click')

    expect(load).toHaveBeenCalledWith({
      from: '2026-07-01',
      to: '2026-09-04',
      query: '合肥',
    })
    wrapper.unmount()
  })
})
