import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useExpenseStore } from '@/stores/expense'
import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'
import type { ReimbursementDraft } from '@/types/reimbursements'
import ExpenseSummaryCard from './ExpenseSummaryCard.vue'

function draft(): ReimbursementDraft {
  return {
    id: 'draft-1',
    status: 'DRAFT',
    revision: 2,
    department: { id: '100', name: '测试部门' },
    templateConfigVersion: 1,
    relatedApprovalCount: 0,
    expiresAt: '2026-10-01T00:00:00Z',
    createdAt: '2026-09-01T00:00:00Z',
    updatedAt: '2026-09-01T00:00:00Z',
    lockedAt: null,
    template: {
      processCode: 'PROC-1',
      configVersion: 1,
      schemaFingerprint: 'a'.repeat(64),
    },
    input: {
      ocrDispositionVersion: 1,
      companyValue: '北京',
      budgetCodeValue: '26007',
      project: { mode: 'manual', text: '测试项目' },
      trip: null,
      dismissedOcrFileIds: [],
      items: [],
    },
    totals: {
      expenseTotal: '0.00',
      subsidyTotal: '0.00',
      totalAmount: '0.00',
      receiptCount: 0,
      uppercaseAmount: '零元整',
      subsidy: null,
    },
    relatedApprovals: [],
    relatedApprovalSummary: null,
  }
}

describe('ExpenseSummaryCard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('downloads only the persisted draft preview and explains final server generation', async () => {
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const preview = vi.spyOn(drafts, 'downloadExcelPreview').mockResolvedValue(undefined)
    const expense = useExpenseStore()
    expense.totals = draft().totals
    const wrapper = mount(ExpenseSummaryCard, {
      global: { plugins: [ElementPlus] },
    })

    const button = wrapper.find('button')
    expect(button.text()).toContain('预览 Excel')
    await button.trigger('click')

    expect(preview).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('正式提交时生成最终报销单和票据汇总 PDF')
    expect(wrapper.text()).not.toContain('生成并下载 Excel')
    wrapper.unmount()
  })

  it('blocks a preview while the form differs from the saved draft', () => {
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const wrapper = mount(ExpenseSummaryCard, {
      props: { previewDisabledReason: '表单有未保存修改，请先保存草稿再预览' },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.find('button').attributes()).toHaveProperty('disabled')
    expect(wrapper.text()).toContain('表单有未保存修改')
    wrapper.unmount()
  })
})
