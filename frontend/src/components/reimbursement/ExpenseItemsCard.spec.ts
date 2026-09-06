import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteReimbursementDraftFile,
  getReimbursementDraft,
  getReimbursementFileContent,
  listReimbursementDraftFiles,
  recognizeReimbursementDraftFile,
  updateReimbursementDraft,
  updateReimbursementDraftFile,
  uploadReimbursementDraftFile,
} from '@/api/reimbursements'
import { logout as logoutRequest } from '@/api/auth'
import {
  deleteReceiptFile,
  recognizeReceiptFile,
  uploadReceiptFile,
} from '@/api/receipts'
import { useExpenseStore } from '@/stores/expense'
import { useAuthStore } from '@/stores/auth'
import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'
import type { OcrReceiptCandidate, ItineraryOcrResult } from '@/types/receipts'
import { receiptOcrResult } from '@/types/reimbursements'
import type {
  ReimbursementDraft,
  ReimbursementDraftFile,
} from '@/types/reimbursements'
import ExpenseItemsCard from './ExpenseItemsCard.vue'

vi.mock('@/api/receipts', () => ({
  deleteReceiptFile: vi.fn(),
  recognizeReceiptFile: vi.fn(),
  uploadReceiptFile: vi.fn(),
}))

vi.mock('@/api/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/auth')>()
  return { ...actual, logout: vi.fn() }
})

vi.mock('@/api/reimbursements', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/reimbursements')>()
  return {
    ...actual,
    deleteReimbursementDraftFile: vi.fn(),
    getReimbursementDraft: vi.fn(),
    getReimbursementFileContent: vi.fn(),
    listReimbursementDraftFiles: vi.fn(),
    recognizeReimbursementDraftFile: vi.fn(),
    updateReimbursementDraft: vi.fn(),
    updateReimbursementDraftFile: vi.fn(),
    uploadReimbursementDraftFile: vi.fn(),
  }
})

function draft(revision = 1, id = 'draft-1'): ReimbursementDraft {
  return {
    id,
    status: 'DRAFT',
    revision,
    department: { id: '100', name: '测试部门' },
    templateConfigVersion: 3,
    relatedApprovalCount: 0,
    expiresAt: '2026-10-04T00:00:00Z',
    createdAt: '2026-09-04T00:00:00Z',
    updatedAt: '2026-09-04T00:01:00Z',
    lockedAt: null,
    template: {
      processCode: 'PROC-REIMBURSEMENT',
      configVersion: 3,
      schemaFingerprint: 'a'.repeat(64),
    },
    input: {
      ocrDispositionVersion: 1,
      companyValue: '北京',
      budgetCodeValue: '26007',
      project: { mode: 'manual', text: '测试项目' },
      trip: null,
      items: [],
      dismissedOcrFileIds: [],
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

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function serverFile(
  id: string,
  name: string,
  role: ReimbursementDraftFile['role'],
  overrides: Partial<ReimbursementDraftFile> = {},
): ReimbursementDraftFile {
  return {
    id,
    name,
    role,
    attachmentKind: 'other',
    sortOrder: Number(id.replace(/\D/g, '')) || 0,
    status: 'ACTIVE',
    mediaType: name.endsWith('.pdf') ? 'application/pdf' : 'image/jpeg',
    sizeBytes: 7,
    ocrStatus: 'NOT_REQUESTED',
    ocrResult: null,
    ...overrides,
  }
}

function recognizedFile(
  id: string,
  name: string,
  amount = '454.00',
): ReimbursementDraftFile & { ocrResult: OcrReceiptCandidate } {
  return { ...serverFile(id, name, 'EXPENSE_SOURCE'),
    ocrStatus: 'COMPLETE',
    ocrResult: {
      fileId: id,
      type: 'train',
      categoryId: 'rail_fare',
      categoryName: '火车票',
      date: '2026-09-01',
      description: `${name} 的行程`,
      amount,
      receiptCount: 1,
      source: 'ocr',
      confidence: '0.93',
      warnings: [],
      status: 'recognized',
      error: null,
    },
  }
}

function selectFiles(wrapper: ReturnType<typeof mount>, testId: string, files: File[]) {
  const input = wrapper.get<HTMLInputElement>(`[data-testid="${testId}"]`)
  Object.defineProperty(input.element, 'files', { configurable: true, value: files })
  return input.trigger('change')
}

function recognizedItinerary(id = 'proof-1'): ReimbursementDraftFile {
  const ocrResult: ItineraryOcrResult = {
    fileId: id, version: 1, kind: 'itinerary', status: 'recognized', source: 'pdf_text',
    pageCount: 1, processedPageCount: 1, complete: true, warnings: [], error: null,
    summary: { currency: 'CNY', amount: '60.00', startDate: '2026-09-01', endDate: '2026-09-01', invoiceNumbers: ['INV-001'], orderNumbers: [] },
    trips: [{ page: 1, row: 1, date: '2026-09-01', amount: '60.00', origin: '合肥机场', destination: '滨湖酒店', invoiceNumbers: [], orderNumbers: [] }],
  }
  return serverFile(id, '行程.pdf', 'ATTACHMENT_ONLY', { attachmentKind: 'itinerary', ocrStatus: 'COMPLETE', ocrResult })
}

function taxiInvoice(): ReimbursementDraftFile & { ocrResult: OcrReceiptCandidate } {
  const file = recognizedFile('invoice-1', '打车发票.pdf', '60.00')
  Object.assign(file.ocrResult, { categoryId: 'local_transport', type: 'ride_hailing', transportType: 'ride_hailing', invoiceNumbers: ['INV-001'], requiresItinerary: true })
  return file
}

describe('ExpenseItemsCard durable files', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    vi.mocked(logoutRequest).mockResolvedValue()
    window.ResizeObserver = class ResizeObserver {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it.each(['invoice_first', 'itinerary_first'])('matches itinerary OCR without creating an expense in %s order and persists through hydration', async (order) => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'local_transport', name: '市内交通', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const invoice = taxiInvoice()
    const proof = recognizedItinerary()
    if (order === 'invoice_first') { drafts.files = [invoice]; expense.upsertDraftOcrItem(invoice) }
    else drafts.files = [proof]
    const fileToUpload = order === 'invoice_first' ? proof : invoice
    vi.mocked(uploadReimbursementDraftFile).mockResolvedValue({ draftId: 'draft-1', revision: 2, file: { ...fileToUpload, ocrResult: null, ocrStatus: 'NOT_REQUESTED' } })
    vi.mocked(recognizeReimbursementDraftFile).mockResolvedValue({ draftId: 'draft-1', revision: 3, file: fileToUpload })
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await selectFiles(wrapper, order === 'invoice_first' ? 'durable-itinerary-input' : 'durable-expense-input', [new File(['pdf'], fileToUpload.name, { type: 'application/pdf' })])
    await flushPromises()
    expect(uploadReimbursementDraftFile).toHaveBeenCalledWith('draft-1', 1, expect.any(File), expect.objectContaining({ attachmentKind: fileToUpload.attachmentKind, role: fileToUpload.role }))
    expect(recognizeReimbursementDraftFile).toHaveBeenCalledOnce()
    expect(expense.items).toHaveLength(1)
    expect(expense.items[0]).toMatchObject({ sourceFileId: 'invoice-1', itineraryFileIds: ['proof-1'], receiptCount: 1 })
    const persisted = expense.buildDraftExpenseItems()
    expense.hydrateFromDraft({ ...draft(4), input: { ...draft().input, items: persisted } }, drafts.files)
    expect(expense.items[0]?.itineraryFileIds).toEqual(['proof-1'])
    expect(expense.buildDraftExpenseItems()).toEqual(persisted)
    wrapper.unmount()
  })

  it('uploads payment proofs without OCR or receipt-count changes', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'local_transport', name: '市内交通', order: 1, manualSelectable: true }]
    useReimbursementDraftStore().currentDraft = draft()
    const file = serverFile('payment-1', '转账.pdf', 'ATTACHMENT_ONLY', { attachmentKind: 'payment_proof' })
    vi.mocked(uploadReimbursementDraftFile).mockResolvedValue({ draftId: 'draft-1', revision: 2, file })
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await selectFiles(wrapper, 'durable-payment-proof-input', [new File(['pdf'], file.name, { type: 'application/pdf' })])
    await flushPromises()
    expect(recognizeReimbursementDraftFile).not.toHaveBeenCalled()
    expect(expense.items).toEqual([])
    expect(expense.buildDraftExpenseItems()).toEqual([])
    expect(wrapper.text()).toContain('付款凭证')
    wrapper.unmount()
  })

  it('lets the employee confirm an unclassified OCR city-transport invoice as taxi and match its itinerary on save', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'local_transport', name: '市内交通', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const invoice = taxiInvoice()
    Object.assign(invoice.ocrResult, { transportType: 'other', requiresItinerary: false })
    drafts.files = [invoice, recognizedItinerary()]
    expense.upsertDraftOcrItem(invoice)
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()
    expect(expense.items[0]?.itineraryFileIds).toEqual([])
    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    const transportField = wrapper.findAllComponents({ name: 'ElFormItem' }).find((field) => field.props('label') === '市内交通类型')
    expect(transportField).toBeDefined()
    transportField!.findComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', 'taxi')
    await flushPromises()
    expect(wrapper.findAllComponents({ name: 'ElFormItem' }).some((field) => field.props('label') === '对应行程单')).toBe(true)
    await wrapper.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
    await flushPromises()
    expect(expense.buildDraftExpenseItems()[0]).toMatchObject({ transportType: 'taxi', requiresItinerary: false, itineraryFileIds: ['proof-1'], itineraryAutoMatchDisabled: false })
    wrapper.unmount()
  })

  it('keeps insufficient evidence manual and retries matching after the employee corrects amount, date and route', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'local_transport', name: '市内交通', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const invoice = taxiInvoice()
    Object.assign(invoice.ocrResult, { transportType: 'other', requiresItinerary: false, invoiceNumbers: [], amount: '70.00', date: '2026-09-02', description: '交通费用' })
    drafts.files = [invoice, recognizedItinerary()]
    expense.upsertDraftOcrItem(invoice)
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    const formItem = (label: string) => wrapper.findAllComponents({ name: 'ElFormItem' }).find((field) => field.props('label') === label)!
    formItem('市内交通类型').findComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', 'taxi')
    await flushPromises()
    expect(formItem('对应行程单').findAllComponents({ name: 'ElOption' }).map((option) => option.props('value'))).toEqual(['proof-1'])
    await wrapper.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
    await flushPromises()
    expect(expense.items[0]?.itineraryFileIds).toEqual([])
    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    formItem('发生日期').findComponent({ name: 'ElDatePicker' }).vm.$emit('update:modelValue', '2026-09-01')
    formItem('金额（元）').findComponent({ name: 'ElInput' }).vm.$emit('update:modelValue', '60.00')
    formItem('说明').findComponent({ name: 'ElInput' }).vm.$emit('update:modelValue', '合肥机场至滨湖酒店')
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
    await flushPromises()
    expect(expense.buildDraftExpenseItems()[0]?.itineraryFileIds).toEqual(['proof-1'])
    wrapper.unmount()
  })

  it('does not let an OCR-confirmed ride-hailing invoice cancel its required itinerary', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'local_transport', name: '市内交通', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const invoice = taxiInvoice()
    drafts.files = [invoice]
    expense.upsertDraftOcrItem(invoice)
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    expect(wrapper.findAllComponents({ name: 'ElFormItem' }).some((field) => field.props('label') === '市内交通类型')).toBe(false)
    expect(wrapper.text()).toContain('网约车费用必须有对应行程单')
    await wrapper.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
    await flushPromises()
    expect(expense.buildDraftExpenseItems()[0]).toMatchObject({ transportType: 'ride_hailing', requiresItinerary: true })
    wrapper.unmount()
  })

  it('keeps a manually cleared itinerary after autosave and refresh until explicit automatic matching is requested', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'local_transport', name: '市内交通', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const invoice = taxiInvoice()
    drafts.files = [invoice, recognizedItinerary()]
    expense.upsertDraftOcrItem(invoice)
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()
    expect(expense.items[0]?.itineraryFileIds).toEqual(['proof-1'])
    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    const itinerary = wrapper.findAllComponents({ name: 'ElFormItem' }).find((field) => field.props('label') === '对应行程单')!
    itinerary.findComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', [])
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
    const input = { ...draft().input, items: expense.buildDraftExpenseItems() }
    expect(input.items[0]?.itineraryFileIds).toEqual([])
    expect(input.items[0]).toMatchObject({ itineraryAutoMatchDisabled: true })
    const pending = deferred<ReimbursementDraft>()
    vi.mocked(updateReimbursementDraft).mockReturnValue(pending.promise)
    const saving = drafts.saveDraft(input)
    await vi.waitFor(() => expect(updateReimbursementDraft).toHaveBeenCalledOnce())
    pending.resolve({ ...draft(2), input })
    await saving
    await flushPromises()
    expect(expense.buildDraftExpenseItems()[0]?.itineraryFileIds).toEqual([])
    wrapper.unmount()
    expense.hydrateFromDraft({ ...draft(2), input }, drafts.files)
    const restored = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()
    expect(expense.buildDraftExpenseItems()[0]).toMatchObject({ itineraryFileIds: [], itineraryAutoMatchDisabled: true })
    await restored.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    await restored.findAll('button').find((button) => button.text() === '重新自动匹配')!.trigger('click')
    await flushPromises()
    expect(expense.buildDraftExpenseItems()[0]).toMatchObject({ itineraryFileIds: ['proof-1'], itineraryAutoMatchDisabled: false })
    restored.unmount()
  })

  it.each(['other', 'local_transport'])('allows unknown rail evidence but explains known non-rail evidence when category is corrected from %s', async (categoryId) => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const source = recognizedFile('file-1', '待确认.pdf', '600.00')
    Object.assign(source.ocrResult, { categoryId, status: categoryId === 'other' ? 'failed' : 'recognized', railType: null })
    drafts.files = [source]
    expense.upsertDraftOcrItem(source)
    expense.items[0]!.category = 'rail_fare'
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    const railField = wrapper.findAllComponents({ name: 'ElFormItem' }).find((field) => field.props('label') === '铁路票种')
    expect(Boolean(railField)).toBe(categoryId === 'other')
    if (railField) {
      railField.findComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', 'high_speed')
      await flushPromises()
      expect(wrapper.findAllComponents({ name: 'ElFormItem' }).some((field) => field.props('label') === '付款凭证')).toBe(false)
      await wrapper.findAll('button').find((button) => button.text() === '保存')!.trigger('click')
      expect(expense.items[0]?.railType).toBe('high_speed')
    } else {
      expect(wrapper.text()).toContain('修改类别不会获得高铁付款凭证豁免')
      expect(wrapper.findAllComponents({ name: 'ElFormItem' }).some((field) => field.props('label') === '付款凭证')).toBe(true)
    }
    wrapper.unmount()
  })

  it('discards a late purpose-change response after switching drafts without OCR or relinking', async () => {
    const pending = deferred<Awaited<ReturnType<typeof updateReimbursementDraftFile>>>()
    const expense = useExpenseStore()
    expense.categories = [{ id: 'local_transport', name: '市内交通', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    drafts.files = [serverFile('proof-1', '原材料.pdf', 'ATTACHMENT_ONLY')]
    vi.mocked(updateReimbursementDraftFile).mockReturnValue(pending.promise)
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await wrapper.findAll('button').find((button) => button.text() === '用途')!.trigger('click')
    await flushPromises()
    const dialog = wrapper.findAllComponents({ name: 'ElDialog' }).find((entry) => entry.props('title') === '修改材料用途')!
    dialog.findComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', 'itinerary')
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '保存用途')!.trigger('click')
    await vi.waitFor(() => expect(updateReimbursementDraftFile).toHaveBeenCalledOnce())
    drafts.currentDraft = draft(1, 'draft-2')
    drafts.files = []
    expense.reset()
    pending.resolve({ draftId: 'draft-1', revision: 2, file: recognizedItinerary() })
    await flushPromises()
    expect(recognizeReimbursementDraftFile).not.toHaveBeenCalled()
    expect(drafts.files).toEqual([])
    expect(expense.items).toEqual([])
    expect(dialog.props('modelValue')).toBe(false)
    wrapper.unmount()
  })

  it('changes an existing material purpose, clears incompatible proof links and recognizes without reupload', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'local_transport', name: '市内交通', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const source = taxiInvoice()
    const proof = recognizedItinerary()
    drafts.files = [source, { ...proof, attachmentKind: 'payment_proof', ocrResult: null }]
    expense.upsertDraftOcrItem(source)
    expense.items[0]!.paymentProofFileIds = [proof.id]
    vi.mocked(updateReimbursementDraftFile).mockResolvedValue({ draftId: 'draft-1', revision: 2, file: { ...proof, ocrResult: null } })
    vi.mocked(recognizeReimbursementDraftFile).mockResolvedValue({ draftId: 'draft-1', revision: 3, file: proof })
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await wrapper.findAll('button').find((button) => button.text() === '用途')!.trigger('click')
    await flushPromises()
    const selector = wrapper.findAllComponents({ name: 'ElDialog' }).find((entry) => entry.props('title') === '修改材料用途')!.findComponent({ name: 'ElSelect' })
    selector.vm.$emit('update:modelValue', 'itinerary')
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '保存用途')!.trigger('click')
    await flushPromises()
    expect(updateReimbursementDraftFile).toHaveBeenCalledWith('draft-1', proof.id, { attachmentKind: 'itinerary', expectedRevision: 1 }, expect.any(Object))
    expect(recognizeReimbursementDraftFile).toHaveBeenCalledOnce()
    expect(uploadReimbursementDraftFile).not.toHaveBeenCalled()
    expect(expense.items[0]).toMatchObject({ itineraryFileIds: [proof.id], paymentProofFileIds: [] })
    expect(expense.items).toHaveLength(1)
    wrapper.unmount()
  })

  it.each([
    { amount: '500.00', category: 'rail_fare', railType: 'unknown', expected: false },
    { amount: '500.01', category: 'rail_fare', railType: 'unknown', expected: true },
    { amount: '600.00', category: 'rail_fare', railType: 'high_speed', expected: false },
    { amount: '600.00', category: 'rail_fare', railType: 'emu', expected: true },
    { amount: '600.00', category: 'local_transport', railType: null, expected: true },
  ] as const)('shows payment candidates for the confirmed row amount and supported rail evidence %j', async ({ amount, category, railType, expected }) => {
    const expense = useExpenseStore()
    expense.categories = [{ id: category, name: '费用类别', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const source = recognizedFile('file-1', '费用.pdf', amount)
    Object.assign(source.ocrResult, { categoryId: category, railType })
    drafts.files = [source, recognizedItinerary(), serverFile('payment-1', '付款.pdf', 'ATTACHMENT_ONLY', { attachmentKind: 'payment_proof' }), serverFile('other-1', '其他.pdf', 'ATTACHMENT_ONLY')]
    expense.upsertDraftOcrItem(source)
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    const paymentField = wrapper.findAllComponents({ name: 'ElFormItem' }).find((field) => field.props('label') === '付款凭证')
    expect(Boolean(paymentField)).toBe(expected)
    if (paymentField) expect(paymentField.findAllComponents({ name: 'ElOption' }).map((option) => option.props('value'))).toEqual(['payment-1'])
    const railField = wrapper.findAllComponents({ name: 'ElFormItem' }).find((field) => field.props('label') === '铁路票种')
    expect(Boolean(railField)).toBe(category === 'rail_fare')
    if (railField) expect(railField.findComponent({ name: 'ElSelect' }).props('disabled')).toBe(railType !== 'unknown')
    wrapper.unmount()
  })

  it('uploads the whole selection before sequential OCR and uploads other attachments without OCR', async () => {
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    vi.mocked(uploadReimbursementDraftFile).mockImplementation(
      async (_draftId, revision, file, options) => ({
        draftId: 'draft-1',
        revision: revision + 1,
        file: serverFile(
          `file-${revision}`,
          file.name,
          options?.role ?? 'EXPENSE_SOURCE',
        ),
      }),
    )
    vi.mocked(recognizeReimbursementDraftFile).mockImplementation(
      async (_draftId, fileId, input) => {
        const uploaded = drafts.files.find((file) => file.id === fileId)!
        return {
          draftId: 'draft-1',
          revision: input.expectedRevision + 1,
          file: recognizedFile(fileId, uploaded.name),
        }
      },
    )
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    await selectFiles(wrapper, 'durable-expense-input', [
      new File(['a'], '发票一.pdf', { type: 'application/pdf' }),
      new File(['b'], '发票二.jpg', { type: 'image/jpeg' }),
    ])
    await flushPromises()
    await selectFiles(wrapper, 'durable-attachment-input', [
      new File(['c'], '行程单.pdf', { type: 'application/pdf' }),
    ])
    await flushPromises()

    expect(uploadReceiptFile).not.toHaveBeenCalled()
    expect(recognizeReceiptFile).not.toHaveBeenCalled()
    expect(deleteReceiptFile).not.toHaveBeenCalled()
    expect(vi.mocked(uploadReimbursementDraftFile).mock.calls.map((call) => [
      call[1], call[2].name, call[3]?.role,
    ])).toEqual([
      [1, '发票一.pdf', 'EXPENSE_SOURCE'],
      [2, '发票二.jpg', 'EXPENSE_SOURCE'],
      [5, '行程单.pdf', 'ATTACHMENT_ONLY'],
    ])
    expect(vi.mocked(recognizeReimbursementDraftFile).mock.calls.map((call) => [
      call[1], call[2].expectedRevision,
    ])).toEqual([
      ['file-1', 3],
      ['file-2', 4],
    ])
    expect(expense.items.map((item) => item.id)).toEqual(['ocr-file-1', 'ocr-file-2'])
    expect(drafts.currentDraft?.revision).toBe(6)
    expect(wrapper.text()).toContain('行程单.pdf')
    expect(wrapper.text()).toContain('其他材料')
    expect(wrapper.text()).toContain('发票一.pdf 的行程')
    expect(wrapper.find('[aria-label="预览票据 发票一.pdf"]').exists()).toBe(true)
    expect(wrapper.findAll('.receipt-row').some((row) => row.text().includes('发票一.pdf'))).toBe(false)

    wrapper.unmount()
  })

  it.each([
    ['hotel', 'hotel', false], ['rail_fare', 'rail', false],
    ['local_transport', 'other', false], ['local_transport', 'taxi', true],
    ['local_transport', 'ride_hailing', true],
  ] as const)('shows itinerary editing only for taxi expenses (%s/%s)', async (category, transportType, visible) => {
    const expense = useExpenseStore()
    expense.categories = [{ id: category, name: category, order: 1, manualSelectable: true }]
    expense.items = [{
      id: 'manual', category, transportType, date: '2026-09-01', displayDate: '2026-09-01',
      description: '本次费用', amount: '10.00', receiptCount: 1, source: 'manual',
    }]
    useReimbursementDraftStore().currentDraft = draft()
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    await flushPromises()
    expect(wrapper.findAllComponents({ name: 'ElFormItem' }).some((item) =>
      String(item.props('label')).includes('对应行程单'),
    )).toBe(visible)
    wrapper.unmount()
  })

  it('keeps a source invoice fixed at one receipt and permits manual aggregate receipt counts', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const source = recognizedFile('file-1', '单张发票.pdf')
    drafts.files = [source]
    expense.upsertDraftOcrItem(source)
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
    await flushPromises()
    expect(wrapper.findAllComponents({ name: 'ElInputNumber' })).toHaveLength(0)
    await wrapper.findAllComponents({ name: 'ElButton' }).find((button) => button.text() === '取消')!.trigger('click')
    await wrapper.findAll('button').find((button) => button.text() === '手动添加')!.trigger('click')
    await flushPromises()
    expect(wrapper.findAllComponents({ name: 'ElInputNumber' })).toHaveLength(1)
    expect(wrapper.findAllComponents({ name: 'ElFormItem' }).find((item) => item.props('label') === '票据张数')!.text())
      .toContain('手工汇总多张票据时填写；行程单和证明材料不计入')
    wrapper.unmount()
  })

  it('keeps every selected placeholder stable and publishes OCR rows together at batch completion', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true }]
    const calculate = vi.spyOn(expense, 'refreshCalculations').mockResolvedValue()
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    const upload = deferred<Awaited<ReturnType<typeof uploadReimbursementDraftFile>>>()
    const recognition = deferred<Awaited<ReturnType<typeof recognizeReimbursementDraftFile>>>()
    vi.mocked(uploadReimbursementDraftFile).mockReturnValueOnce(upload.promise)
      .mockImplementation(async (draftId, revision, file) => ({
        draftId, revision: revision + 1, file: serverFile('file-2', file.name, 'EXPENSE_SOURCE'),
      }))
    vi.mocked(recognizeReimbursementDraftFile)
      .mockResolvedValueOnce({ draftId: 'draft-1', revision: 4, file: recognizedFile('file-1', '第一张.pdf') })
      .mockReturnValueOnce(recognition.promise)
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await selectFiles(wrapper, 'durable-expense-input', [
      new File(['a'], '第一张.pdf', { type: 'application/pdf' }),
      new File(['b'], '第二张.pdf', { type: 'application/pdf' }),
    ])
    expect(wrapper.findAll('[data-testid="batch-file"]')).toHaveLength(2)
    const placeholders = wrapper.findAll('[data-testid="batch-file"]').map((row) => row.element)
    expect(recognizeReimbursementDraftFile).not.toHaveBeenCalled()
    upload.resolve({ draftId: 'draft-1', revision: 2, file: serverFile('file-1', '第一张.pdf', 'EXPENSE_SOURCE') })
    await flushPromises()
    expect(uploadReimbursementDraftFile).toHaveBeenCalledTimes(2)
    expect(recognizeReimbursementDraftFile).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('[data-testid="batch-file"]').map((row) => row.element)).toEqual(placeholders)
    expect(expense.items).toHaveLength(0)
    expect(calculate).not.toHaveBeenCalled()
    recognition.resolve({ draftId: 'draft-1', revision: 5, file: recognizedFile('file-2', '第二张.pdf') })
    await flushPromises()
    expect(expense.items).toHaveLength(2)
    expect(wrapper.get('[data-testid="batch-progress"]').text()).toContain('本批 2 个文件已处理完成')
    expect(wrapper.findAll('[data-testid="batch-file"]')).toHaveLength(0)
    expect(wrapper.get('[data-testid="batch-progress"]').text()).not.toContain('第一张.pdf')
    expect(calculate).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('continues the batch after individual upload and OCR failures without replaying either request', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    let revision = 1
    const retained: ReimbursementDraftFile[] = []
    vi.mocked(getReimbursementDraft).mockImplementation(async () => draft(revision))
    vi.mocked(listReimbursementDraftFiles).mockImplementation(async () => ({ draftId: 'draft-1', revision, items: [...retained] }))
    vi.mocked(uploadReimbursementDraftFile).mockImplementation(async (draftId, expected, file) => {
      expect(expected).toBe(revision)
      if (file.name === '上传失败.pdf') throw new Error('单张上传失败')
      const uploaded = serverFile(`file-${revision}`, file.name, 'EXPENSE_SOURCE')
      retained.push(uploaded)
      return { draftId, revision: ++revision, file: uploaded }
    })
    vi.mocked(recognizeReimbursementDraftFile).mockImplementation(async (draftId, fileId, input) => {
      expect(input.expectedRevision).toBe(revision)
      const uploaded = retained.find((file) => file.id === fileId)!
      if (uploaded.name === '识别失败.pdf') throw new Error('单张识别失败')
      return { draftId, revision: ++revision, file: recognizedFile(fileId, uploaded.name) }
    })
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await selectFiles(wrapper, 'durable-expense-input', ['上传失败.pdf', '识别失败.pdf', '成功一.pdf', '成功二.pdf'].map((name) =>
      new File(['pdf'], name, { type: 'application/pdf' }),
    ))
    await flushPromises()
    expect(uploadReimbursementDraftFile).toHaveBeenCalledTimes(4)
    expect(recognizeReimbursementDraftFile).toHaveBeenCalledTimes(3)
    expect(expense.items.map((item) => item.description)).toEqual(['成功一.pdf 的行程', '成功二.pdf 的行程'])
    expect(wrapper.get('[data-testid="batch-progress"]').text()).toContain('单张上传失败')
    expect(wrapper.get('[data-testid="batch-progress"]').text()).toContain('单张识别失败')
    expect(drafts.processingFiles).toBe(false)
    wrapper.unmount()
  })

  it('does not start a batch when the file picker is cancelled', async () => {
    useReimbursementDraftStore().currentDraft = draft()
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await selectFiles(wrapper, 'durable-expense-input', [])
    expect(uploadReimbursementDraftFile).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="batch-progress"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('lets a manual city-transport row explicitly choose taxi or ride-hailing and preserves the requirement', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'local_transport', name: '市内交通', order: 1, manualSelectable: true }]
    expense.upsertManualItem({ category: 'local_transport', date: '2026-09-01', displayDate: '2026-09-01', description: '市内交通', amount: '20.00', receiptCount: 3 })
    useReimbursementDraftStore().currentDraft = draft()
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    for (const transportType of ['taxi', 'ride_hailing'] as const) {
      await wrapper.findAll('button').find((button) => button.text() === '编辑')!.trigger('click')
      await flushPromises()
      const selector = wrapper.findAllComponents({ name: 'ElFormItem' }).find((item) => item.props('label') === '市内交通类型')!
        .findComponent({ name: 'ElSelect' })
      selector.vm.$emit('update:modelValue', transportType)
      await flushPromises()
      expect(wrapper.findAllComponents({ name: 'ElFormItem' }).some((item) => String(item.props('label')).includes('对应行程单'))).toBe(true)
      expect(wrapper.findAllComponents({ name: 'ElFormItem' }).find((item) => item.props('label') === '票据张数')!.text()).toContain('付款凭证按本行金额判断')
      await wrapper.findAllComponents({ name: 'ElButton' }).find((button) => button.text() === '保存')!.trigger('click')
      expect(expense.items[0]).toMatchObject({ transportType, requiresItinerary: transportType === 'ride_hailing', receiptCount: 3 })
    }
    wrapper.unmount()
  })

  it('allows durable upload, OCR retry, and deletion while the draft is review ready', async () => {
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = { ...draft(8), status: 'REVIEW_READY' }
    const failed = recognizedFile('file-1', '待重试发票.pdf', '10.00')
    failed.ocrStatus = 'FAILED'
    failed.ocrResult = {
      ...failed.ocrResult!,
      status: 'failed',
      error: { code: 'OCR_FAILED', message: '识别失败，请重试' },
    }
    drafts.files = [failed]
    expense.upsertDraftOcrItem(failed)
    vi.mocked(recognizeReimbursementDraftFile).mockResolvedValue({
      draftId: 'draft-1',
      revision: 9,
      file: recognizedFile('file-1', '待重试发票.pdf', '20.00'),
    })
    vi.mocked(uploadReimbursementDraftFile).mockImplementation(
      async (draftId, revision, file, options) => ({
        draftId,
        revision: revision + 1,
        file: serverFile('file-2', file.name, options?.role ?? 'EXPENSE_SOURCE'),
      }),
    )
    vi.mocked(deleteReimbursementDraftFile).mockResolvedValue({
      draftId: 'draft-1',
      revision: 11,
      deletedFileId: 'file-1',
    })
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue({} as never)
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const retry = wrapper.findAll('button').find((button) =>
      button.text().trim() === '重新识别',
    )
    expect(retry?.attributes('disabled')).toBeUndefined()
    await retry!.trigger('click')
    await flushPromises()
    expect(recognizeReimbursementDraftFile).toHaveBeenCalledWith(
      'draft-1',
      'file-1',
      expect.objectContaining({ expectedRevision: 8 }),
      expect.any(Object),
    )

    drafts.currentDraft!.status = 'REVIEW_READY'
    await selectFiles(wrapper, 'durable-attachment-input', [
      new File(['a'], '行程单.pdf', { type: 'application/pdf' }),
    ])
    await flushPromises()
    expect(uploadReimbursementDraftFile).toHaveBeenCalledWith(
      'draft-1',
      9,
      expect.objectContaining({ name: '行程单.pdf' }),
      expect.objectContaining({ role: 'ATTACHMENT_ONLY' }),
    )

    drafts.currentDraft!.status = 'REVIEW_READY'
    const sourceRow = wrapper.findAll('.el-table__row').find((row) =>
      row.text().includes('待重试发票.pdf'),
    )
    const remove = sourceRow!.findAll('button').find((button) =>
      button.text().trim() === '删除',
    )
    expect(remove?.attributes('disabled')).toBeUndefined()
    await remove!.trigger('click')
    await flushPromises()
    expect(deleteReimbursementDraftFile).toHaveBeenCalledWith(
      'draft-1',
      'file-1',
      10,
      expect.any(Object),
    )

    wrapper.unmount()
  })

  it.each([
    ['LOCKED', '当前报销已提交，不能再修改附件'],
    ['EXPIRED', '当前报销已过期，不能再修改附件'],
  ] as const)(
    'disables every durable file mutation when the draft is %s',
    async (status, reason) => {
      const expense = useExpenseStore()
      expense.categories = [
        { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
      ]
      const drafts = useReimbursementDraftStore()
      drafts.currentDraft = { ...draft(8), status }
      const failed = recognizedFile('file-1', '只读发票.pdf', '10.00')
      failed.ocrStatus = 'FAILED'
      drafts.files = [failed]
      expense.upsertDraftOcrItem(failed)
      const wrapper = mount(ExpenseItemsCard, {
        props: { durable: true },
        global: { plugins: [pinia, ElementPlus] },
      })

      const uploadButtons = wrapper.findAll('button').filter((button) =>
        ['选择票据/发票', '添加行程单'].includes(button.text().trim()),
      )
      expect(uploadButtons).toHaveLength(2)
      for (const button of uploadButtons) {
        expect(button.attributes('disabled')).toBeDefined()
        expect(button.attributes('title')).toBe(reason)
      }
      expect(wrapper.get('[data-testid="durable-expense-input"]').attributes('disabled')).toBeDefined()
      expect(wrapper.get('[data-testid="durable-attachment-input"]').attributes('disabled')).toBeDefined()

      await flushPromises()
      const sourceRow = wrapper.findAll('.el-table__row').find((row) =>
        row.text().includes('只读发票.pdf'),
      )!
      const mutationButtons = sourceRow.findAll('button').filter((button) => ['重新识别', '删除'].includes(button.text().trim()))
      expect(mutationButtons.map((button) => button.text().trim())).toEqual([
        '重新识别',
        '删除',
      ])
      for (const button of mutationButtons) {
        expect(button.attributes('disabled')).toBeDefined()
        expect(button.attributes('title')).toBe(reason)
      }

      await selectFiles(wrapper, 'durable-expense-input', [
        new File(['a'], '禁止上传.pdf', { type: 'application/pdf' }),
      ])
      await mutationButtons[0]!.trigger('click')
      await mutationButtons[1]!.trigger('click')
      await flushPromises()
      expect(uploadReimbursementDraftFile).not.toHaveBeenCalled()
      expect(recognizeReimbursementDraftFile).not.toHaveBeenCalled()
      expect(deleteReimbursementDraftFile).not.toHaveBeenCalled()

      wrapper.unmount()
    },
  )

  it('blocks every durable item and file mutation while the parent operation is read only', async () => {
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const failed = recognizedFile('file-1', '操作中只读发票.pdf', '10.00')
    failed.ocrStatus = 'FAILED'
    failed.ocrResult = {
      ...failed.ocrResult!,
      status: 'failed',
      error: { code: 'OCR_FAILED', message: '识别失败，请重试' },
    }
    drafts.files = [failed]
    expense.upsertDraftOcrItem(failed)
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true, readonly: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const mutationLabels = new Set([
      '手动添加',
      '选择票据/发票',
      '添加行程单',
      '添加付款凭证',
      '添加其他材料',
      '重新识别',
      '删除文件',
      '编辑',
      '删除',
    ])
    const mutationButtons = wrapper.findAll('button').filter((button) =>
      mutationLabels.has(button.text().trim()),
    )
    expect(mutationButtons.length).toBeGreaterThanOrEqual(5)
    for (const button of mutationButtons) {
      expect(button.attributes()).toHaveProperty('disabled')
    }
    expect(wrapper.get('[data-testid="durable-expense-input"]').attributes())
      .toHaveProperty('disabled')
    expect(wrapper.get('[data-testid="durable-attachment-input"]').attributes())
      .toHaveProperty('disabled')

    await selectFiles(wrapper, 'durable-expense-input', [
      new File(['a'], '禁止上传.pdf', { type: 'application/pdf' }),
    ])
    for (const button of mutationButtons) await button.trigger('click')
    await flushPromises()

    expect(uploadReimbursementDraftFile).not.toHaveBeenCalled()
    expect(recognizeReimbursementDraftFile).not.toHaveBeenCalled()
    expect(deleteReimbursementDraftFile).not.toHaveBeenCalled()
    expect(expense.items).toEqual([
      expect.objectContaining({ id: 'ocr-file-1', amount: '10.00' }),
    ])

    wrapper.unmount()
  })

  it('stops an old upload before OCR or remaining files when another draft is opened', async () => {
    const pending = deferred<Awaited<ReturnType<typeof uploadReimbursementDraftFile>>>()
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    vi.mocked(uploadReimbursementDraftFile).mockReturnValueOnce(pending.promise)
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const operation = selectFiles(wrapper, 'durable-expense-input', [
      new File(['a'], '旧草稿一.pdf', { type: 'application/pdf' }),
      new File(['b'], '旧草稿二.pdf', { type: 'application/pdf' }),
    ])
    await vi.waitFor(() => expect(uploadReimbursementDraftFile).toHaveBeenCalledOnce())
    drafts.currentDraft = draft(1, 'draft-2')
    drafts.files = []
    pending.resolve({
      draftId: 'draft-1',
      revision: 2,
      file: serverFile('file-old', '旧草稿一.pdf', 'EXPENSE_SOURCE'),
    })
    await operation
    await flushPromises()

    expect(uploadReimbursementDraftFile).toHaveBeenCalledOnce()
    expect(recognizeReimbursementDraftFile).not.toHaveBeenCalled()
    expect(expense.items).toEqual([])
    expect(drafts.currentDraft?.id).toBe('draft-2')

    wrapper.unmount()
  })

  it('does not insert an old OCR result or continue its batch after switching drafts', async () => {
    const pending = deferred<Awaited<ReturnType<typeof recognizeReimbursementDraftFile>>>()
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    vi.mocked(uploadReimbursementDraftFile).mockImplementation(
      async (draftId, revision, file, options) => ({
        draftId,
        revision: revision + 1,
        file: serverFile(`file-${revision}`, file.name, options?.role ?? 'EXPENSE_SOURCE'),
      }),
    )
    vi.mocked(recognizeReimbursementDraftFile)
      .mockReturnValueOnce(pending.promise)
      .mockImplementation(async (draftId, fileId, input) => ({
        draftId,
        revision: input.expectedRevision + 1,
        file: recognizedFile(fileId, '不应上传.pdf'),
      }))
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const operation = selectFiles(wrapper, 'durable-expense-input', [
      new File(['a'], '旧草稿一.pdf', { type: 'application/pdf' }),
      new File(['b'], '旧草稿二.pdf', { type: 'application/pdf' }),
    ])
    await vi.waitFor(() => expect(recognizeReimbursementDraftFile).toHaveBeenCalledOnce())
    const current = draft(1, 'draft-2')
    current.input.items = [{
      category: 'rail_fare',
      date: '2026-09-02',
      displayDate: '2026-09-02',
      description: '新草稿已有明细',
      amount: '1.00',
      receiptCount: 1,
    }]
    drafts.currentDraft = current
    drafts.files = []
    expense.hydrateFromDraft(current, [])
    pending.resolve({
      draftId: 'draft-1',
      revision: 3,
      file: recognizedFile('file-1', '旧草稿一.pdf'),
    })
    await operation
    await flushPromises()

    expect(uploadReimbursementDraftFile).toHaveBeenCalledTimes(2)
    expect(recognizeReimbursementDraftFile).toHaveBeenCalledOnce()
    expect(expense.items).toEqual([
      expect.objectContaining({ description: '新草稿已有明细', amount: '1.00' }),
    ])
    expect(expense.items.some((item) => item.id === 'ocr-file-1')).toBe(false)

    wrapper.unmount()
  })

  it.each(['reset', 'logout'] as const)(
    'silently stops an in-flight batch after %s clears the draft scope',
    async (ending) => {
      const pending = deferred<Awaited<ReturnType<typeof uploadReimbursementDraftFile>>>()
      const drafts = useReimbursementDraftStore()
      drafts.currentDraft = draft()
      vi.mocked(uploadReimbursementDraftFile).mockReturnValueOnce(pending.promise)
      const message = vi.spyOn(ElMessage, 'error')
      const wrapper = mount(ExpenseItemsCard, {
        props: { durable: true },
        global: { plugins: [pinia, ElementPlus] },
      })

      const operation = selectFiles(wrapper, 'durable-expense-input', [
        new File(['a'], '旧会话一.pdf', { type: 'application/pdf' }),
        new File(['b'], '旧会话二.pdf', { type: 'application/pdf' }),
      ])
      await vi.waitFor(() => expect(uploadReimbursementDraftFile).toHaveBeenCalledOnce())
      if (ending === 'logout') await useAuthStore().logout()
      else drafts.reset()
      pending.resolve({
        draftId: 'draft-1',
        revision: 2,
        file: serverFile('file-old', '旧会话一.pdf', 'EXPENSE_SOURCE'),
      })
      await operation
      await flushPromises()

      expect(uploadReimbursementDraftFile).toHaveBeenCalledOnce()
      expect(recognizeReimbursementDraftFile).not.toHaveBeenCalled()
      expect(message).not.toHaveBeenCalled()

      wrapper.unmount()
    },
  )

  it('does not continue an in-flight batch after the component is unmounted', async () => {
    const pending = deferred<Awaited<ReturnType<typeof uploadReimbursementDraftFile>>>()
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft()
    vi.mocked(uploadReimbursementDraftFile).mockReturnValueOnce(pending.promise)
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const operation = selectFiles(wrapper, 'durable-expense-input', [
      new File(['a'], '卸载前一.pdf', { type: 'application/pdf' }),
      new File(['b'], '卸载前二.pdf', { type: 'application/pdf' }),
    ])
    await vi.waitFor(() => expect(uploadReimbursementDraftFile).toHaveBeenCalledOnce())
    wrapper.unmount()
    pending.resolve({
      draftId: 'draft-1',
      revision: 2,
      file: serverFile('file-old', '卸载前一.pdf', 'EXPENSE_SOURCE'),
    })
    await operation
    await flushPromises()

    expect(uploadReimbursementDraftFile).toHaveBeenCalledOnce()
    expect(recognizeReimbursementDraftFile).not.toHaveBeenCalled()
  })

  it('retries persistent OCR without duplication and confirms linked deletion', async () => {
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const failed = recognizedFile('file-1', '待重试发票.pdf', '10.00')
    failed.ocrStatus = 'FAILED'
    failed.ocrResult = {
      ...failed.ocrResult!,
      status: 'failed',
      error: { code: 'OCR_FAILED', message: '识别失败，请重试' },
    }
    drafts.files = [failed]
    expense.upsertDraftOcrItem(failed)
    vi.mocked(recognizeReimbursementDraftFile).mockResolvedValue({
      draftId: 'draft-1',
      revision: 9,
      file: recognizedFile('file-1', '待重试发票.pdf', '20.00'),
    })
    vi.mocked(deleteReimbursementDraftFile).mockResolvedValue({
      draftId: 'draft-1',
      revision: 10,
      deletedFileId: 'file-1',
    })
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue({} as never)
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const retry = wrapper.findAll('button').find((button) =>
      button.text().trim() === '重新识别',
    )
    expect(retry).toBeDefined()
    await retry!.trigger('click')
    await flushPromises()

    expect(expense.items).toEqual([
      expect.objectContaining({ id: 'ocr-file-1', amount: '20.00' }),
    ])
    const remove = wrapper.findAll('button').find((button) =>
      ['删除', '删除文件'].includes(button.text().trim()),
    )
    expect(remove).toBeDefined()
    await remove!.trigger('click')
    await flushPromises()

    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('费用明细'),
      expect.any(String),
      expect.any(Object),
    )
    expect(deleteReimbursementDraftFile).toHaveBeenCalledWith(
      'draft-1',
      'file-1',
      9,
      expect.any(Object),
    )
    expect(expense.items).toEqual([])
    expect(expense.dismissedOcrFileIds).toEqual([])
    expect(drafts.files).toEqual([])

    wrapper.unmount()
  })

  it('previews server-held invoices and shows linked itinerary only with the expense row', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const source = recognizedFile('file-1', '发票.pdf', '10.00')
    const itinerary = serverFile('file-2', '行程单.pdf', 'ATTACHMENT_ONLY', { attachmentKind: 'itinerary' })
    drafts.files = [source, itinerary]
    expense.upsertDraftOcrItem(source)
    expense.items[0]!.itineraryFileIds = ['file-2']
    expense.items[0]!.requiresItinerary = true
    const blob = new Blob(['pdf'], { type: 'application/pdf' })
    vi.mocked(getReimbursementFileContent).mockResolvedValue(blob)
    const createUrl = vi.fn().mockReturnValue('blob:server-preview')
    const revokeUrl = vi.fn()
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = createUrl
      static revokeObjectURL = revokeUrl
    })
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()

    expect(wrapper.findAll('.receipt-row')).toHaveLength(0)
    const desktopRow = wrapper.find('.el-table__row')
    expect(desktopRow.text()).toContain('发票.pdf')
    expect(desktopRow.text()).toContain('行程单.pdf')
    expect(desktopRow.text()).not.toContain('缺少行程单')
    expect(desktopRow.findAll('button').filter((button) => button.text().trim() === '删除')).toHaveLength(1)
    await wrapper.get('[aria-label="预览票据 发票.pdf"]').trigger('click')
    await flushPromises()
    expect(getReimbursementFileContent).toHaveBeenCalledWith('draft-1', 'file-1', { signal: expect.any(AbortSignal) })
    expect(createUrl).toHaveBeenCalledWith(blob)
    expect(wrapper.get('iframe').attributes('src')).toBe('blob:server-preview')
    wrapper.unmount()
    expect(revokeUrl).toHaveBeenCalledWith('blob:server-preview')
  })

  it('allows explicit reuse of a multi-trip itinerary for another expense', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const first = recognizedFile('file-1', '发票一.pdf')
    const second = recognizedFile('file-2', '发票二.pdf')
    for (const source of [first, second]) {
      source.ocrResult = { ...source.ocrResult!, transportType: 'ride_hailing', requiresItinerary: true }
    }
    const itinerary = serverFile('file-3', '多次行程.pdf', 'ATTACHMENT_ONLY', { attachmentKind: 'itinerary' })
    drafts.files = [first, second, itinerary]
    expense.upsertDraftOcrItem(first)
    expense.upsertDraftOcrItem(second)
    expense.items[0]!.itineraryFileIds = ['file-3']
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()
    const secondRow = wrapper.findAll('.el-table__row').find((row) => row.text().includes('发票二.pdf'))!
    await secondRow.findAll('button').find((button) => button.text().trim() === '编辑')!.trigger('click')
    await flushPromises()
    const option = wrapper.findAllComponents({ name: 'ElOption' }).find((entry) => entry.props('value') === 'file-3')!
    expect(option.props('disabled')).toBe(false)
    const selector = wrapper.findAllComponents({ name: 'ElSelect' }).find((entry) => entry.props('multiple'))!
    selector.vm.$emit('update:modelValue', ['file-3'])
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text().trim() === '保存')!.trigger('click')
    expect(expense.items.map((item) => item.itineraryFileIds)).toEqual([['file-3'], ['file-3']])
    expect(expense.items[1]?.itineraryAutoMatchDisabled).toBe(true)
    expect(wrapper.findAll('button').some((button) => button.text() === '重新自动匹配')).toBe(false)
    expect(wrapper.text()).toContain('汇总 PDF 中只保留一份')
    wrapper.unmount()
  })

  it('lets an employee confirm RMB for an unknown-currency foreign invoice without guessing its currency', async () => {
    const expense = useExpenseStore()
    expense.categories = [{ id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const source = recognizedFile('file-foreign', '海外发票.jpg')
    source.ocrResult = {
      ...source.ocrResult!, type: 'foreign_receipt', amount: null, originalCurrency: null,
      warnings: ['FOREIGN_CURRENCY_REQUIRES_CNY_AMOUNT'],
    }
    drafts.files = [source]
    expense.upsertDraftOcrItem(source)
    const wrapper = mount(ExpenseItemsCard, { props: { durable: true }, global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text().trim() === '编辑')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('人民币报销金额（元）')
    const amountInput = wrapper.findAllComponents({ name: 'ElInput' }).find((input) => input.props('placeholder') === '0.00')!
    amountInput.vm.$emit('update:modelValue', '700.00')
    const confirmation = wrapper.findAllComponents({ name: 'ElCheckbox' }).find((checkbox) => checkbox.text().includes('已核对原币金额'))!
    confirmation.vm.$emit('update:modelValue', true)
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text().trim() === '保存')!.trigger('click')
    expect(expense.items[0]).toMatchObject({ amount: '700.00', requiresCnyConfirmation: true, cnyAmountConfirmed: true })
    expect(expense.items[0]?.originalCurrency).toBeUndefined()
    wrapper.unmount()
  })

  it('shows an unresolved OCR blocker and requires confirmation before ignoring it', async () => {
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    drafts.files = [recognizedFile('file-1', '待确认票据.pdf', '10.00')]
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue({} as never)
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    expect(wrapper.text()).toContain('1 张票据的 OCR 结果待确认')
    expect(wrapper.text()).toContain('添加到费用明细')
    const ignore = wrapper.findAll('button').find((button) =>
      button.text().trim() === '仅作为材料保留',
    )
    expect(ignore).toBeDefined()

    await ignore!.trigger('click')
    await flushPromises()

    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('不计入费用金额'),
      '忽略此票据',
      expect.any(Object),
    )
    expect(expense.items).toEqual([])
    expect(expense.dismissedOcrFileIds).toEqual(['file-1'])
    expect(wrapper.text()).not.toContain('OCR 结果待确认')

    wrapper.unmount()
  })

  it.each(['switch', 'department', 'clear', 'unmount'] as const)(
    'drops a late ignore confirmation after draft scope %s',
    async (ending) => {
      const expense = useExpenseStore()
      expense.categories = [
        { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
      ]
      const drafts = useReimbursementDraftStore()
      const auth = useAuthStore()
      auth.status = 'authenticated'
      auth.session = {
        user: { userId: 'synthetic-user', name: '测试用户' },
        departments: [
          { id: '100', name: '测试部门' },
          { id: '200', name: '另一部门' },
        ],
        selectedDepartment: { id: '100', name: '测试部门' },
        isAdmin: false,
        csrfToken: 'synthetic-csrf',
      }
      drafts.currentDraft = draft(8)
      drafts.files = [recognizedFile('file-1', '旧草稿票据.pdf', '10.00')]
      const pending = deferred<Awaited<ReturnType<typeof ElMessageBox.confirm>>>()
      vi.spyOn(ElMessageBox, 'confirm').mockReturnValue(pending.promise)
      const wrapper = mount(ExpenseItemsCard, {
        props: { durable: true },
        global: { plugins: [pinia, ElementPlus] },
      })
      const ignore = wrapper.findAll('button').find((button) =>
        button.text().trim() === '仅作为材料保留',
      )

      const operation = ignore!.trigger('click')
      await vi.waitFor(() => expect(ElMessageBox.confirm).toHaveBeenCalledOnce())
      if (ending === 'switch') {
        drafts.currentDraft = draft(1, 'draft-2')
        drafts.files = []
        expense.hydrateFromDraft(drafts.currentDraft, [])
      } else if (ending === 'department') {
        auth.session = {
          ...auth.session!,
          selectedDepartment: { id: '200', name: '另一部门' },
        }
      } else if (ending === 'clear') {
        drafts.currentDraft = null
        drafts.files = []
        expense.reset()
      } else {
        wrapper.unmount()
      }
      pending.resolve({} as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
      await operation
      await flushPromises()

      expect(expense.dismissedOcrFileIds).toEqual([])
      if (ending !== 'unmount') wrapper.unmount()
    },
  )

  it('keeps a linked item changed by the user while an OCR retry is in flight', async () => {
    const pending = deferred<Awaited<ReturnType<typeof recognizeReimbursementDraftFile>>>()
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const source = recognizedFile('file-1', '人工修改中的票据.pdf', '10.00')
    source.ocrStatus = 'FAILED'
    source.ocrResult = {
      ...source.ocrResult!,
      status: 'failed',
      error: { code: 'OCR_FAILED', message: '识别失败，请重试' },
    }
    drafts.files = [source]
    expense.upsertDraftOcrItem(source)
    vi.mocked(recognizeReimbursementDraftFile).mockReturnValueOnce(pending.promise)
    const warning = vi.spyOn(ElMessage, 'warning')
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const retry = wrapper.findAll('button').find((button) =>
      button.text().trim() === '重新识别',
    )
    await retry!.trigger('click')
    await vi.waitFor(() => expect(recognizeReimbursementDraftFile).toHaveBeenCalledOnce())

    expense.upsertManualItem({
      id: 'ocr-file-1',
      category: 'rail_fare',
      date: '2026-09-02',
      displayDate: '2026-09-02',
      description: '用户在重试期间人工修改',
      amount: '99.00',
      receiptCount: 2,
      warnings: [],
    })
    pending.resolve({
      draftId: 'draft-1',
      revision: 9,
      file: recognizedFile('file-1', '人工修改中的票据.pdf', '20.00'),
    })
    await flushPromises()

    expect(expense.items).toEqual([
      expect.objectContaining({
        id: 'ocr-file-1',
        date: '2026-09-02',
        description: '用户在重试期间人工修改',
        amount: '99.00',
        receiptCount: 1,
      }),
    ])
    expect(receiptOcrResult(drafts.files[0])?.amount).toBe('20.00')
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('未自动新增或覆盖'))

    wrapper.unmount()
  })

  it.each([
    { requiresItinerary: true },
    { transportType: 'ride_hailing' as const },
    { itineraryFileIds: ['itinerary-1'] },
    { originalCurrency: 'VND' },
    { originalAmount: '97600000.00' },
    { cnyAmountConfirmed: true },
    { requiresCnyConfirmation: true },
    { paymentProofFileIds: ['payment-1'] },
    { railType: 'high_speed' as const },
    { itineraryAutoMatchDisabled: true },
  ])('preserves metadata-only edits during an OCR retry (%j)', async (changedFields) => {
    const pending = deferred<Awaited<ReturnType<typeof recognizeReimbursementDraftFile>>>()
    const expense = useExpenseStore()
    expense.categories = [{ id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true }]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const source = recognizedFile('file-1', '待核对票据.pdf', '10.00')
    drafts.files = [source,
      serverFile('itinerary-1', '行程单.pdf', 'ATTACHMENT_ONLY', { attachmentKind: 'itinerary' }),
      serverFile('payment-1', '付款.pdf', 'ATTACHMENT_ONLY', { attachmentKind: 'payment_proof' }),
    ]
    expense.upsertDraftOcrItem(source)
    vi.mocked(recognizeReimbursementDraftFile).mockReturnValueOnce(pending.promise)
    const warning = vi.spyOn(ElMessage, 'warning')
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true }, global: { plugins: [pinia, ElementPlus] },
    })
    const retry = wrapper.findAll('button').find((button) => button.text().trim() === '重新识别')
    await retry!.trigger('click')
    await vi.waitFor(() => expect(recognizeReimbursementDraftFile).toHaveBeenCalledOnce())
    Object.assign(expense.items[0]!, changedFields)
    pending.resolve({
      draftId: 'draft-1', revision: 9,
      file: recognizedFile('file-1', '待核对票据.pdf', '20.00'),
    })
    await flushPromises()
    expect(expense.items[0]).toEqual(expect.objectContaining({ ...changedFields, amount: '10.00' }))
    expect(receiptOcrResult(drafts.files[0])?.amount).toBe('20.00')
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('未自动新增或覆盖'))
    wrapper.unmount()
  })

  it('removes the linked item when a lost delete response reloads an absent file', async () => {
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const source = recognizedFile('file-1', '已在服务端删除.pdf', '10.00')
    drafts.files = [source]
    expense.upsertDraftOcrItem(source)
    vi.mocked(deleteReimbursementDraftFile).mockRejectedValue(new Error('response lost'))
    vi.mocked(getReimbursementDraft).mockResolvedValue(draft(9))
    vi.mocked(listReimbursementDraftFiles).mockResolvedValue({
      draftId: 'draft-1',
      revision: 9,
      items: [],
    })
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue({} as never)
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const remove = wrapper.findAll('button').find((button) =>
      ['删除', '删除文件'].includes(button.text().trim()),
    )
    await remove!.trigger('click')
    await flushPromises()

    expect(drafts.currentDraft?.revision).toBe(9)
    expect(drafts.files).toEqual([])
    expect(expense.items).toEqual([])
    expect(expense.dismissedOcrFileIds).toEqual([])

    wrapper.unmount()
  })

  it('keeps the linked item when a lost delete response reloads the existing file', async () => {
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const source = recognizedFile('file-1', '仍在服务端.pdf', '10.00')
    drafts.files = [source]
    expense.upsertDraftOcrItem(source)
    vi.mocked(deleteReimbursementDraftFile).mockRejectedValue(new Error('response lost'))
    vi.mocked(getReimbursementDraft).mockResolvedValue(draft(8))
    vi.mocked(listReimbursementDraftFiles).mockResolvedValue({
      draftId: 'draft-1',
      revision: 8,
      items: [source],
    })
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue({} as never)
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const remove = wrapper.findAll('button').find((button) =>
      ['删除', '删除文件'].includes(button.text().trim()),
    )
    await remove!.trigger('click')
    await flushPromises()

    expect(drafts.files).toEqual([source])
    expect(expense.items).toEqual([
      expect.objectContaining({ id: 'ocr-file-1', amount: '10.00' }),
    ])
    expect(wrapper.text()).toContain('已同步报销内容最新状态')

    wrapper.unmount()
  })

  it('does not recreate a dismissed OCR item during retry without explicit adoption', async () => {
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const current = draft(8)
    current.input.items = [{
      category: 'rail_fare',
      date: '2026-09-02',
      displayDate: '2026-09-02',
      description: '用户保存的人工修改',
      amount: '10.00',
      receiptCount: 1,
    }]
    current.input.dismissedOcrFileIds = ['file-1']
    const source = recognizedFile('file-1', '人工修改票据.pdf', '10.00')
    source.ocrStatus = 'FAILED'
    source.ocrResult = {
      ...source.ocrResult!,
      date: '2026-09-01',
      description: '旧 OCR 内容',
      status: 'failed',
      error: { code: 'OCR_FAILED', message: '识别失败，请重试' },
    }
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = current
    drafts.files = [source]
    expense.hydrateFromDraft(current, [source])
    vi.mocked(recognizeReimbursementDraftFile).mockResolvedValue({
      draftId: 'draft-1',
      revision: 9,
      file: recognizedFile('file-1', '人工修改票据.pdf', '20.00'),
    })
    const warning = vi.spyOn(ElMessage, 'warning')
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const retry = wrapper.findAll('button').find((button) =>
      button.text().trim() === '重新识别',
    )
    await retry!.trigger('click')
    await flushPromises()

    expect(expense.items).toEqual([
      expect.objectContaining({
        id: 'draft-draft-1-item-0',
        description: '用户保存的人工修改',
        amount: '10.00',
      }),
    ])
    expect(expense.items.some((item) => item.id === 'ocr-file-1')).toBe(false)
    expect(expense.dismissedOcrFileIds).toEqual(['file-1'])
    expect(receiptOcrResult(drafts.files[0])?.amount).toBe('20.00')
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('未自动新增或覆盖'))
    expect(wrapper.findAll('button').some((button) =>
      button.text().trim() === '添加到费用明细',
    )).toBe(true)

    wrapper.unmount()
  })
})
