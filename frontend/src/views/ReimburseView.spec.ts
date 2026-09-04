/* eslint-disable vue/one-component-per-file */
import ElementPlus, { ElMessageBox } from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick } from 'vue'

import { calculateTotals } from '@/api/expenses'
import { fetchReadiness } from '@/api/health'
import { searchProjects } from '@/api/projects'
import {
  createReimbursementDraft,
  getOaReimbursementOptions,
  getOaReimbursementSubmissionForDraft,
  getReimbursementDraft,
  listOaTravelApprovals,
  listReimbursementDraftFiles,
  listReimbursementDrafts,
  markReimbursementDraftReviewReady,
  replaceReimbursementRelatedApprovals,
  submitOaReimbursement,
  updateReimbursementDraft,
} from '@/api/reimbursements'
import { useAuthStore } from '@/stores/auth'
import { useExpenseStore } from '@/stores/expense'
import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'
import type {
  ReimbursementDraft,
  ReimbursementDraftFile,
  ReimbursementDraftInput,
  ReimbursementRelatedApproval,
  ReimbursementRelatedApprovalSelection,
  ReimbursementSubmission,
} from '@/types/reimbursements'
import ReimburseView from './ReimburseView.vue'

vi.mock('@/api/health', () => ({ fetchReadiness: vi.fn() }))
vi.mock('@/api/projects', () => ({ searchProjects: vi.fn() }))
vi.mock('@/api/expenses', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/expenses')>()
  return { ...actual, calculateTotals: vi.fn() }
})
vi.mock('@/api/reimbursements', () => ({
  createReimbursementDraft: vi.fn(),
  deleteReimbursementDraft: vi.fn(),
  deleteReimbursementDraftFile: vi.fn(),
  getOaReimbursementOptions: vi.fn(),
  getOaReimbursementSubmission: vi.fn(),
  getOaReimbursementSubmissionForDraft: vi.fn(),
  getReimbursementDraft: vi.fn(),
  getReimbursementDraftExcelPreview: vi.fn(),
  listOaTravelApprovals: vi.fn(),
  listReimbursementDraftFiles: vi.fn(),
  listReimbursementDrafts: vi.fn(),
  markReimbursementDraftReviewReady: vi.fn(),
  recognizeReimbursementDraftFile: vi.fn(),
  replaceReimbursementRelatedApprovals: vi.fn(),
  submitOaReimbursement: vi.fn(),
  updateReimbursementDraft: vi.fn(),
  updateReimbursementDraftFile: vi.fn(),
  uploadReimbursementDraftFile: vi.fn(),
}))

const options = {
  templateConfigVersion: 12,
  reimbursementProcessCode: 'PROC-REIMBURSEMENT',
  companyOptions: [{ value: '北京', label: '北京分公司', key: 'beijing' }],
  budgetCodeOptions: [{ value: '26007', label: '26007 · MES 项目', key: null }],
  travelProfiles: [{
    profileKey: 'business',
    displayName: '境内出差',
    processCode: 'PROC-TRAVEL',
    schemaFingerprint: 'b'.repeat(64),
    travelTypeOption: { value: 'business', label: '境内出差', key: null },
  }],
}

const baseInput: ReimbursementDraftInput = {
  ocrDispositionVersion: 1,
  companyValue: '北京',
  budgetCodeValue: '26007',
  project: { mode: 'manual', text: '合肥长鑫前道 MES 项目' },
  trip: null,
  dismissedOcrFileIds: [],
  items: [{
    sourceFileId: 'file-1',
    category: 'local_transport',
    date: '2026-09-01',
    displayDate: '2026-09-01',
    description: '机场至酒店',
    amount: '44.89',
    receiptCount: 1,
  }],
}

const linkedApproval: ReimbursementRelatedApproval = {
  processInstanceId: 'travel-instance-1',
  profileKey: 'business',
  sourceProcessCode: 'PROC-TRAVEL',
  title: '合肥出差申请',
  businessId: 'TRAVEL-20260901',
  startDate: '2026-08-31',
  endDate: '2026-09-02',
  queryWindow: {
    startTimeMs: Date.parse('2026-08-01T00:00:00+08:00'),
    endTimeMs: Date.parse('2026-09-04T23:59:59.999+08:00'),
  },
  verifiedAt: '2026-09-04T01:00:00Z',
}

const selection: ReimbursementRelatedApprovalSelection = {
  processInstanceId: 'travel-instance-1',
  profileKey: 'business',
  queryWindow: { from: '2026-08-01', to: '2026-09-04' },
}

const activeFile: ReimbursementDraftFile = {
  id: 'file-1',
  name: '打车发票.pdf',
  role: 'EXPENSE_SOURCE',
  sortOrder: 0,
  status: 'ACTIVE',
  mediaType: 'application/pdf',
  sizeBytes: 128,
  ocrStatus: 'COMPLETE',
  ocrResult: null,
}

function makeDraft(overrides: Partial<ReimbursementDraft> = {}): ReimbursementDraft {
  return {
    id: 'draft-1',
    status: 'DRAFT',
    revision: 1,
    department: { id: '100', name: '测试部门' },
    templateConfigVersion: 12,
    relatedApprovalCount: 0,
    expiresAt: '2026-10-04T00:00:00Z',
    createdAt: '2026-09-04T00:00:00Z',
    updatedAt: '2026-09-04T00:00:00Z',
    lockedAt: null,
    template: {
      processCode: 'PROC-REIMBURSEMENT',
      configVersion: 12,
      schemaFingerprint: 'a'.repeat(64),
    },
    input: structuredClone(baseInput),
    totals: {
      expenseTotal: '44.89',
      subsidyTotal: '0.00',
      totalAmount: '44.89',
      receiptCount: 1,
      uppercaseAmount: '肆拾肆元捌角玖分',
      subsidy: null,
    },
    relatedApprovals: [],
    relatedApprovalSummary: null,
    ...overrides,
  }
}

function submissionResult(
  overrides: Partial<ReimbursementSubmission> = {},
): ReimbursementSubmission {
  return {
    submissionId: 'submission-1',
    draftId: 'draft-1',
    status: 'QUEUED',
    statusVersion: 1,
    attemptCount: 0,
    processInstanceId: null,
    businessId: null,
    approvalUrl: null,
    error: null,
    pollAfterMs: 1_500,
    createdAt: '2026-09-04T00:00:00Z',
    updatedAt: '2026-09-04T00:00:00Z',
    submittedAt: null,
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const ExpenseItemsCardStub = defineComponent({
  name: 'ExpenseItemsCard',
  props: {
    durable: { type: Boolean, default: false },
    readonly: { type: Boolean, default: false },
  },
  template: `<section
    data-testid="expense-items"
    :data-durable="String(durable)"
    :data-readonly="String(readonly)"
  >费用明细</section>`,
})
const TripSubsidyCardStub = defineComponent({
  name: 'TripSubsidyCard',
  template: '<section>出差补助</section>',
})
const ExpenseSummaryCardStub = defineComponent({
  name: 'ExpenseSummaryCard',
  props: { previewDisabledReason: { type: String, default: '' } },
  template: '<section data-testid="expense-summary">费用合计</section>',
})
const TravelApprovalSelectorStub = defineComponent({
  name: 'TravelApprovalSelector',
  props: {
    modelValue: { type: Array, required: true },
    linkedApprovals: { type: Array, default: () => [] },
    readonly: { type: Boolean, default: false },
  },
  emits: ['update:modelValue'],
  template: '<section data-testid="travel-selector">关联出差审批</section>',
})

let serverDraft: ReimbursementDraft
let serverFiles: ReimbursementDraftFile[]

function installServerMocks(): void {
  vi.mocked(fetchReadiness).mockResolvedValue({
    status: 'ready',
    checks: { database: 'ok', excelTemplate: 'ok', tempStorage: 'ok', ocr: 'disabled' },
  })
  vi.mocked(searchProjects).mockResolvedValue([
    { id: 101, projectCode: 'P-101', projectName: '内部项目一', enabled: true },
  ])
  vi.mocked(calculateTotals).mockResolvedValue(serverDraft.totals)
  vi.mocked(getOaReimbursementOptions).mockResolvedValue(options)
  vi.mocked(listReimbursementDrafts).mockImplementation(async () => ({
    items: [serverDraft],
    offset: 0,
    limit: 50,
    total: 1,
  }))
  vi.mocked(getReimbursementDraft).mockImplementation(async () => serverDraft)
  vi.mocked(listReimbursementDraftFiles).mockImplementation(async () => ({
    draftId: serverDraft.id,
    revision: serverDraft.revision,
    items: serverFiles,
  }))
  vi.mocked(listOaTravelApprovals).mockResolvedValue({
    templateConfigVersion: 12,
    queryWindow: selection.queryWindow,
    items: [],
  })
  vi.mocked(updateReimbursementDraft).mockImplementation(
    async (_draftId, expectedRevision, input) => {
      serverDraft = {
        ...serverDraft,
        status: 'DRAFT',
        revision: expectedRevision + 1,
        input: structuredClone(input),
        updatedAt: '2026-09-04T00:01:00Z',
      }
      return serverDraft
    },
  )
  vi.mocked(replaceReimbursementRelatedApprovals).mockImplementation(
    async (_draftId, expectedRevision, selections) => {
      const hasSelection = selections.length > 0
      serverDraft = {
        ...serverDraft,
        status: 'DRAFT',
        revision: expectedRevision + 1,
        relatedApprovalCount: selections.length,
        relatedApprovals: hasSelection ? [linkedApproval] : [],
        relatedApprovalSummary: hasSelection
          ? { count: 1, startDate: linkedApproval.startDate, endDate: linkedApproval.endDate }
          : null,
      }
      return serverDraft
    },
  )
  vi.mocked(markReimbursementDraftReviewReady).mockImplementation(
    async (_draftId, expectedRevision) => {
      serverDraft = {
        ...serverDraft,
        status: 'REVIEW_READY',
        revision: expectedRevision + 1,
      }
      return serverDraft
    },
  )
  vi.mocked(createReimbursementDraft).mockImplementation(async (input) => {
    serverDraft = makeDraft({ id: 'draft-created', input, revision: 1 })
    serverFiles = []
    return serverDraft
  })
  vi.mocked(submitOaReimbursement).mockResolvedValue(submissionResult())
}

async function mountView(): Promise<{
  wrapper: VueWrapper
  expense: ReturnType<typeof useExpenseStore>
  drafts: ReturnType<typeof useReimbursementDraftStore>
}> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.status = 'authenticated'
  auth.session = {
    user: { userId: 'synthetic-user', name: '测试用户' },
    departments: [{ id: '100', name: '测试部门' }],
    selectedDepartment: { id: '100', name: '测试部门' },
    isAdmin: true,
    csrfToken: 'synthetic-csrf',
  }
  const expense = useExpenseStore()
  expense.categories = [
    { id: 'local_transport', name: '市内交通费', order: 1, manualSelectable: true },
  ]
  const wrapper = mount(ReimburseView, {
    attachTo: '#test-app',
    global: {
      plugins: [pinia, ElementPlus],
      stubs: {
        ExpenseItemsCard: ExpenseItemsCardStub,
        ExpenseSummaryCard: ExpenseSummaryCardStub,
        RouterLink: { template: '<a><slot /></a>' },
        TravelApprovalSelector: TravelApprovalSelectorStub,
        TripSubsidyCard: TripSubsidyCardStub,
      },
    },
  })
  await flushPromises()
  return { wrapper, expense, drafts: useReimbursementDraftStore() }
}

function visibleButton(wrapper: VueWrapper, label: string) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().trim() === label)
  if (!button) throw new Error(`Missing button: ${label}`)
  return button
}

describe('ReimburseView persistent OA orchestration', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="test-app"></div>'
    window.sessionStorage.clear()
    window.ResizeObserver = class ResizeObserver {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
    window.requestAnimationFrame = (callback: FrameRequestCallback) => {
      callback(0)
      return 0
    }
    window.cancelAnimationFrame = () => undefined
    vi.clearAllMocks()
    serverDraft = makeDraft()
    serverFiles = [activeFile]
    installServerMocks()
  })

  afterEach(() => {
    document.body.innerHTML = ''
    window.sessionStorage.clear()
    vi.restoreAllMocks()
  })

  it('loads all catalogs in parallel, restores the newest draft and distinguishes OA fields', async () => {
    const { wrapper, expense, drafts } = await mountView()

    expect(searchProjects).toHaveBeenCalledOnce()
    expect(getOaReimbursementOptions).toHaveBeenCalledOnce()
    expect(listReimbursementDrafts).toHaveBeenCalledOnce()
    expect(getReimbursementDraft).toHaveBeenCalledWith(
      'draft-1',
      { signal: expect.any(AbortSignal) },
    )
    expect(drafts.reimbursementOptions).toEqual(options)
    expect(expense.manualProjectText).toBe('合肥长鑫前道 MES 项目')
    expect(expense.items).toHaveLength(1)
    expect(wrapper.text()).toContain('OA 所属公司')
    expect(wrapper.text()).toContain('OA 预算代码')
    expect(wrapper.text()).toContain('报销 Excel 内部项目')
    expect(wrapper.text()).not.toContain('仅保存在本页内存中')
    expect(wrapper.findComponent(ExpenseItemsCardStub).props('durable')).toBe(true)

    wrapper.unmount()
  })

  it('creates an empty durable draft from schema options before receipts are added', async () => {
    vi.mocked(listReimbursementDrafts).mockResolvedValue({
      items: [], offset: 0, limit: 50, total: 0,
    })
    const { wrapper } = await mountView()

    await visibleButton(wrapper, '新建报销草稿').trigger('click')
    await nextTick()
    const selects = wrapper.findAllComponents({ name: 'ElSelect' })
    const company = selects.find((item) => item.props('ariaLabel') === '新草稿 OA 所属公司')
    const budget = selects.find((item) => item.props('ariaLabel') === '新草稿 OA 预算代码')
    const project = selects.find((item) => item.props('ariaLabel') === '新草稿选择 Excel 内部项目')
    if (!company || !budget || !project) throw new Error('Missing new draft selectors')
    company.vm.$emit('update:modelValue', '北京')
    budget.vm.$emit('update:modelValue', '26007')
    project.vm.$emit('update:modelValue', 101)
    await nextTick()

    await visibleButton(wrapper, '创建草稿并开始填写').trigger('click')
    await flushPromises()

    expect(createReimbursementDraft).toHaveBeenCalledWith(
      {
        ocrDispositionVersion: 1,
        companyValue: '北京',
        budgetCodeValue: '26007',
        project: { mode: 'selected', id: 101 },
        trip: null,
        items: [],
        dismissedOcrFileIds: [],
      },
      { signal: expect.any(AbortSignal) },
    )
    expect(wrapper.findComponent(ExpenseItemsCardStub).props('durable')).toBe(true)

    wrapper.unmount()
  })

  it('blocks a legacy unresolved OCR row until the user explicitly ignores it', async () => {
    serverDraft = makeDraft({
      input: {
        ...structuredClone(baseInput),
        ocrDispositionVersion: 0,
        items: baseInput.items.map((item) => {
          const legacyItem = { ...item }
          delete legacyItem.sourceFileId
          return legacyItem
        }),
      },
    })
    const { wrapper, expense } = await mountView()

    expect(expense.items).toHaveLength(1)
    expect(expense.items[0]?.sourceFileId).toBeUndefined()
    expect(expense.dismissedOcrFileIds).toEqual([])
    expect(wrapper.text()).toContain('1 张票据的 OCR 结果尚未决定')
    expect(wrapper.text()).toContain('有未保存修改')

    await visibleButton(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(updateReimbursementDraft).not.toHaveBeenCalled()

    expense.dismissDraftOcrFile('file-1')
    await nextTick()
    await visibleButton(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(updateReimbursementDraft).toHaveBeenCalledWith(
      'draft-1',
      1,
      expect.objectContaining({
        ocrDispositionVersion: 1,
        dismissedOcrFileIds: ['file-1'],
        items: [expect.objectContaining({ amount: '44.89' })],
      }),
      { signal: expect.any(AbortSignal) },
    )
    expect(expense.items).toHaveLength(1)
    expect(wrapper.text()).toContain('当前内容已保存')

    wrapper.unmount()
  })

  it('explicitly saves edited form data with the current server revision', async () => {
    const { wrapper, expense, drafts } = await mountView()
    expense.manualProjectText = '修改后的内部项目'
    await nextTick()

    expect(wrapper.text()).toContain('有未保存修改')
    await visibleButton(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(updateReimbursementDraft).toHaveBeenCalledWith(
      'draft-1',
      1,
      expect.objectContaining({
        companyValue: '北京',
        budgetCodeValue: '26007',
        project: { mode: 'manual', text: '修改后的内部项目' },
      }),
      { signal: expect.any(AbortSignal) },
    )
    expect(drafts.currentDraft?.revision).toBe(2)
    expect(wrapper.text()).toContain('当前内容已保存')

    wrapper.unmount()
  })

  it('saves a persisted OCR candidate that was not yet linked in the draft input', async () => {
    serverDraft = makeDraft({
      input: { ...structuredClone(baseInput), items: [] },
      totals: {
        expenseTotal: '0.00',
        subsidyTotal: '0.00',
        totalAmount: '0.00',
        receiptCount: 0,
        uppercaseAmount: '零元整',
        subsidy: null,
      },
    })
    serverFiles = [{
      ...activeFile,
      ocrResult: {
        fileId: 'file-1',
        type: 'taxi',
        categoryId: 'local_transport',
        categoryName: '市内交通费',
        date: '2026-09-01',
        description: '机场至酒店',
        amount: '44.89',
        receiptCount: 1,
        source: 'ocr',
        confidence: '0.95',
        warnings: [],
        status: 'recognized',
        error: null,
      },
    }]
    const { wrapper, expense } = await mountView()

    expect(expense.items).toEqual([
      expect.objectContaining({ sourceFileId: 'file-1', amount: '44.89' }),
    ])
    expect(wrapper.text()).toContain('有未保存修改')
    await visibleButton(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(updateReimbursementDraft).toHaveBeenCalledWith(
      'draft-1',
      1,
      expect.objectContaining({
        dismissedOcrFileIds: [],
        items: [expect.objectContaining({
          sourceFileId: 'file-1',
          amount: '44.89',
        })],
      }),
      { signal: expect.any(AbortSignal) },
    )
    expect(wrapper.text()).toContain('当前内容已保存')

    wrapper.unmount()
  })

  it('persists a dismissed OCR line so saving and hydration do not revive it', async () => {
    serverDraft = makeDraft({
      input: { ...structuredClone(baseInput), items: [] },
    })
    serverFiles = [{
      ...activeFile,
      ocrResult: {
        fileId: 'file-1',
        type: 'taxi',
        categoryId: 'local_transport',
        categoryName: '市内交通费',
        date: '2026-09-01',
        description: '机场至酒店',
        amount: '44.89',
        receiptCount: 1,
        source: 'ocr',
        confidence: '0.95',
        warnings: [],
        status: 'recognized',
        error: null,
      },
    }]
    const { wrapper, expense } = await mountView()
    expect(expense.items).toHaveLength(1)

    expense.removeItem('ocr-file-1')
    await nextTick()
    await visibleButton(wrapper, '保存草稿').trigger('click')
    await flushPromises()

    expect(updateReimbursementDraft).toHaveBeenCalledWith(
      'draft-1',
      1,
      expect.objectContaining({
        items: [],
        dismissedOcrFileIds: ['file-1'],
      }),
      { signal: expect.any(AbortSignal) },
    )
    expect(expense.items).toEqual([])
    expect(expense.dismissedOcrFileIds).toEqual(['file-1'])
    expect(wrapper.text()).toContain('当前内容已保存')

    wrapper.unmount()
  })

  it('does not let a slow save overwrite a newer OCR disposition', async () => {
    serverDraft = makeDraft({ input: { ...structuredClone(baseInput), items: [] } })
    serverFiles = [{
      ...activeFile,
      ocrResult: {
        fileId: 'file-1',
        type: 'taxi',
        categoryId: 'local_transport',
        categoryName: '市内交通费',
        date: '2026-09-01',
        description: '机场至酒店',
        amount: '44.89',
        receiptCount: 1,
        source: 'ocr',
        confidence: '0.95',
        warnings: [],
        status: 'recognized',
        error: null,
      },
    }]
    const pending = deferred<ReimbursementDraft>()
    vi.mocked(updateReimbursementDraft).mockReturnValueOnce(pending.promise)
    const { wrapper, expense } = await mountView()

    const saving = visibleButton(wrapper, '保存草稿').trigger('click')
    await vi.waitFor(() => expect(updateReimbursementDraft).toHaveBeenCalledOnce())
    const requestedInput = vi.mocked(updateReimbursementDraft).mock.calls[0]![2]
    expect(requestedInput.items[0]?.sourceFileId).toBe('file-1')

    expense.removeItem('ocr-file-1')
    pending.resolve(makeDraft({ revision: 2, input: structuredClone(requestedInput) }))
    await saving
    await flushPromises()

    expect(expense.items).toEqual([])
    expect(expense.dismissedOcrFileIds).toEqual(['file-1'])
    expect(wrapper.text()).toContain('有未保存修改')

    wrapper.unmount()
  })

  it('locks every draft interaction during a slow save and preserves newer local input', async () => {
    const pending = deferred<ReimbursementDraft>()
    vi.mocked(updateReimbursementDraft).mockReturnValueOnce(pending.promise)
    const { wrapper, expense } = await mountView()
    expense.manualProjectText = '保存请求中的项目'
    await nextTick()

    const saving = visibleButton(wrapper, '保存草稿').trigger('click')
    await vi.waitFor(() => expect(updateReimbursementDraft).toHaveBeenCalledOnce())

    expect(wrapper.get('.plain-fieldset').attributes()).toHaveProperty('disabled')
    expect(wrapper.get('.editor-fieldset').attributes()).toHaveProperty('disabled')
    expect(wrapper.findComponent(ExpenseItemsCardStub).props('readonly')).toBe(true)
    expect(wrapper.findComponent(TravelApprovalSelectorStub).props('readonly')).toBe(true)
    expect(wrapper.findComponent(ExpenseSummaryCardStub).props('previewDisabledReason'))
      .toContain('当前操作')
    const draftSelect = wrapper.findAllComponents({ name: 'ElSelect' }).find(
      (component) => component.props('ariaLabel') === '选择报销草稿',
    )
    expect(draftSelect?.props('disabled')).toBe(true)
    expect(visibleButton(wrapper, '新建草稿').attributes()).toHaveProperty('disabled')
    expect(visibleButton(wrapper, '确认并提交到钉钉 OA').attributes()).toHaveProperty('disabled')

    expense.manualProjectText = '请求发出后的新内容'
    pending.resolve(makeDraft({
      revision: 2,
      input: {
        ...structuredClone(baseInput),
        project: { mode: 'manual', text: '保存请求中的项目' },
      },
    }))
    await saving
    await flushPromises()

    expect(expense.manualProjectText).toBe('请求发出后的新内容')
    expect(wrapper.text()).toContain('有未保存修改')

    wrapper.unmount()
  })

  it('saves input, verifies related approvals, marks review-ready and posts only once', async () => {
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue(undefined as never)
    const { wrapper } = await mountView()
    wrapper.findComponent(TravelApprovalSelectorStub).vm.$emit(
      'update:modelValue',
      [selection],
    )
    await nextTick()

    const submit = visibleButton(wrapper, '确认并提交到钉钉 OA')
    await Promise.all([submit.trigger('click'), submit.trigger('click')])
    await flushPromises()

    expect(confirm).toHaveBeenCalledOnce()
    expect(updateReimbursementDraft).toHaveBeenCalledOnce()
    expect(replaceReimbursementRelatedApprovals).toHaveBeenCalledWith(
      'draft-1',
      2,
      [selection],
      { signal: expect.any(AbortSignal) },
    )
    expect(markReimbursementDraftReviewReady).toHaveBeenCalledWith(
      'draft-1',
      3,
      { signal: expect.any(AbortSignal) },
    )
    expect(submitOaReimbursement).toHaveBeenCalledOnce()
    expect(vi.mocked(submitOaReimbursement).mock.calls[0]?.slice(0, 2)).toEqual([
      'draft-1',
      4,
    ])
    expect(vi.mocked(updateReimbursementDraft).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(replaceReimbursementRelatedApprovals).mock.invocationCallOrder[0]!)
    expect(vi.mocked(replaceReimbursementRelatedApprovals).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(markReimbursementDraftReviewReady).mock.invocationCallOrder[0]!)
    expect(vi.mocked(markReimbursementDraftReviewReady).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(submitOaReimbursement).mock.invocationCallOrder[0]!)
    expect(wrapper.text()).toContain('已提交，等待处理')

    wrapper.unmount()
  })

  it('keeps every draft interaction locked through confirm, save, related, review and submit', async () => {
    const confirmation = deferred<Awaited<ReturnType<typeof ElMessageBox.confirm>>>()
    const savedInput = deferred<ReimbursementDraft>()
    const savedRelated = deferred<ReimbursementDraft>()
    const reviewReady = deferred<ReimbursementDraft>()
    const submitted = deferred<ReimbursementSubmission>()
    vi.spyOn(ElMessageBox, 'confirm').mockReturnValueOnce(confirmation.promise)
    vi.mocked(updateReimbursementDraft).mockReturnValueOnce(savedInput.promise)
    vi.mocked(replaceReimbursementRelatedApprovals).mockReturnValueOnce(savedRelated.promise)
    vi.mocked(markReimbursementDraftReviewReady).mockReturnValueOnce(reviewReady.promise)
    vi.mocked(submitOaReimbursement).mockReturnValueOnce(submitted.promise)
    const { wrapper } = await mountView()
    wrapper.findComponent(TravelApprovalSelectorStub).vm.$emit(
      'update:modelValue',
      [selection],
    )
    await nextTick()

    const assertLocked = () => {
      expect(wrapper.get('.plain-fieldset').attributes()).toHaveProperty('disabled')
      expect(wrapper.get('.editor-fieldset').attributes()).toHaveProperty('disabled')
      expect(wrapper.findComponent(ExpenseItemsCardStub).props('readonly')).toBe(true)
      expect(wrapper.findComponent(TravelApprovalSelectorStub).props('readonly')).toBe(true)
      expect(wrapper.findComponent(ExpenseSummaryCardStub).props('previewDisabledReason'))
        .toContain('当前操作')
      const draftSelect = wrapper.findAllComponents({ name: 'ElSelect' }).find(
        (component) => component.props('ariaLabel') === '选择报销草稿',
      )
      expect(draftSelect?.props('disabled')).toBe(true)
      expect(visibleButton(wrapper, '新建草稿').attributes()).toHaveProperty('disabled')
    }

    void visibleButton(wrapper, '确认并提交到钉钉 OA').trigger('click')
    await vi.waitFor(() => expect(ElMessageBox.confirm).toHaveBeenCalledOnce())
    assertLocked()

    confirmation.resolve(undefined as never)
    await vi.waitFor(() => expect(updateReimbursementDraft).toHaveBeenCalledOnce())
    assertLocked()

    savedInput.resolve(makeDraft({ revision: 2 }))
    await vi.waitFor(() => expect(replaceReimbursementRelatedApprovals).toHaveBeenCalledOnce())
    assertLocked()

    savedRelated.resolve(makeDraft({
      revision: 3,
      relatedApprovalCount: 1,
      relatedApprovals: [linkedApproval],
      relatedApprovalSummary: {
        count: 1,
        startDate: linkedApproval.startDate,
        endDate: linkedApproval.endDate,
      },
    }))
    await vi.waitFor(() => expect(markReimbursementDraftReviewReady).toHaveBeenCalledOnce())
    assertLocked()

    reviewReady.resolve(makeDraft({
      status: 'REVIEW_READY',
      revision: 4,
      relatedApprovalCount: 1,
      relatedApprovals: [linkedApproval],
      relatedApprovalSummary: {
        count: 1,
        startDate: linkedApproval.startDate,
        endDate: linkedApproval.endDate,
      },
    }))
    await vi.waitFor(() => expect(submitOaReimbursement).toHaveBeenCalledOnce())
    assertLocked()

    submitted.resolve(submissionResult())
    await flushPromises()
    expect(wrapper.findComponent(ExpenseItemsCardStub).props('readonly')).toBe(true)
    expect(wrapper.findComponent(TravelApprovalSelectorStub).props('readonly')).toBe(true)

    wrapper.unmount()
  })

  it('restores a locked cross-device submission with GET and exposes only a real OA URL', async () => {
    serverDraft = makeDraft({
      status: 'LOCKED',
      revision: 5,
      lockedAt: '2026-09-04T00:02:00Z',
      relatedApprovalCount: 1,
      relatedApprovals: [linkedApproval],
      relatedApprovalSummary: {
        count: 1,
        startDate: linkedApproval.startDate,
        endDate: linkedApproval.endDate,
      },
    })
    vi.mocked(getOaReimbursementSubmissionForDraft).mockResolvedValue(submissionResult({
      status: 'SUBMITTED',
      statusVersion: 8,
      processInstanceId: 'oa-process-1',
      businessId: 'OA-20260904001',
      approvalUrl: 'dingtalk://dingtalkclient/action/openapp?process=oa-process-1',
      pollAfterMs: 0,
      submittedAt: '2026-09-04T00:03:00Z',
    }))
    const { wrapper } = await mountView()

    expect(getOaReimbursementSubmissionForDraft).toHaveBeenCalledWith(
      'draft-1',
      { signal: expect.any(AbortSignal) },
    )
    expect(submitOaReimbursement).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('审批已成功发起')
    expect(wrapper.text()).toContain('oa-process-1')
    expect(wrapper.text()).toContain('OA-20260904001')
    expect(wrapper.find('[data-testid="approval-link"]').attributes('href'))
      .toBe('dingtalk://dingtalkclient/action/openapp?process=oa-process-1')
    expect(wrapper.find('.editor-fieldset').attributes()).toHaveProperty('disabled')
    expect(visibleButton(wrapper, '确认并提交到钉钉 OA').attributes()).toHaveProperty('disabled')

    wrapper.unmount()
  })

  it('keeps a final failure locked and never invents an OA link', async () => {
    serverDraft = makeDraft({ status: 'LOCKED', revision: 5, lockedAt: '2026-09-04T00:02:00Z' })
    vi.mocked(getOaReimbursementSubmissionForDraft).mockResolvedValue(submissionResult({
      status: 'FAILED_FINAL',
      statusVersion: 8,
      error: { code: 'OA_CREATE_REJECTED', message: '钉钉拒绝发起审批' },
      pollAfterMs: 0,
    }))
    const { wrapper } = await mountView()

    expect(wrapper.text()).toContain('审批未发起')
    expect(wrapper.text()).toContain('钉钉拒绝发起审批')
    expect(wrapper.find('[data-testid="approval-link"]').exists()).toBe(false)
    expect(wrapper.find('.editor-fieldset').attributes()).toHaveProperty('disabled')
    expect(submitOaReimbursement).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})
