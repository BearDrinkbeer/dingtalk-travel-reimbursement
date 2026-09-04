import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteReimbursementDraftFile,
  getReimbursementDraft,
  listReimbursementDraftFiles,
  recognizeReimbursementDraftFile,
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
    listReimbursementDraftFiles: vi.fn(),
    recognizeReimbursementDraftFile: vi.fn(),
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
): ReimbursementDraftFile {
  return serverFile(id, name, 'EXPENSE_SOURCE', {
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
  })
}

function selectFiles(wrapper: ReturnType<typeof mount>, testId: string, files: File[]) {
  const input = wrapper.get<HTMLInputElement>(`[data-testid="${testId}"]`)
  Object.defineProperty(input.element, 'files', { configurable: true, value: files })
  return input.trigger('change')
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
  })

  it('uploads receipt files sequentially with OCR and uploads other attachments without OCR', async () => {
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
      [3, '发票二.jpg', 'EXPENSE_SOURCE'],
      [5, '行程单.pdf', 'ATTACHMENT_ONLY'],
    ])
    expect(vi.mocked(recognizeReimbursementDraftFile).mock.calls.map((call) => [
      call[1], call[2].expectedRevision,
    ])).toEqual([
      ['file-1', 2],
      ['file-3', 4],
    ])
    expect(expense.items.map((item) => item.id)).toEqual(['ocr-file-1', 'ocr-file-3'])
    expect(drafts.currentDraft?.revision).toBe(6)
    expect(wrapper.text()).toContain('行程单.pdf')
    expect(wrapper.text()).toContain('其他附件')
    expect(wrapper.text()).toContain('发票一.pdf 的行程')
    expect(wrapper.find('[aria-label="预览票据 发票一.pdf"]').exists()).toBe(false)

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
    const sourceRow = wrapper.findAll('.receipt-row').find((row) =>
      row.text().includes('待重试发票.pdf'),
    )
    const remove = sourceRow!.findAll('button').find((button) =>
      button.text().trim() === '删除文件',
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
    ['LOCKED', '当前草稿已锁定，不能再修改附件'],
    ['EXPIRED', '当前草稿已过期，不能再修改附件'],
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
        ['选择票据/发票', '添加其他附件'].includes(button.text().trim()),
      )
      expect(uploadButtons).toHaveLength(2)
      for (const button of uploadButtons) {
        expect(button.attributes('disabled')).toBeDefined()
        expect(button.attributes('title')).toBe(reason)
      }
      expect(wrapper.get('[data-testid="durable-expense-input"]').attributes('disabled')).toBeDefined()
      expect(wrapper.get('[data-testid="durable-attachment-input"]').attributes('disabled')).toBeDefined()

      const sourceRow = wrapper.findAll('.receipt-row').find((row) =>
        row.text().includes('只读发票.pdf'),
      )!
      const mutationButtons = sourceRow.findAll('button')
      expect(mutationButtons.map((button) => button.text().trim())).toEqual([
        '重新识别',
        '删除文件',
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
      '添加其他附件',
      '重新识别',
      '删除文件',
      '编辑',
      '删除',
    ])
    const mutationButtons = wrapper.findAll('button').filter((button) =>
      mutationLabels.has(button.text().trim()),
    )
    expect(mutationButtons.length).toBeGreaterThanOrEqual(7)
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

    expect(uploadReimbursementDraftFile).toHaveBeenCalledOnce()
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
      button.text().trim() === '删除文件',
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

  it('dismisses a linked OCR line without deleting its file and explicitly adopts it again', async () => {
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'rail_fare', name: '火车票', order: 1, manualSelectable: true },
    ]
    const drafts = useReimbursementDraftStore()
    drafts.currentDraft = draft(8)
    const source = recognizedFile('file-1', '保留附件.pdf', '10.00')
    drafts.files = [source]
    expense.upsertDraftOcrItem(source)
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue({} as never)
    const wrapper = mount(ExpenseItemsCard, {
      props: { durable: true },
      global: { plugins: [pinia, ElementPlus] },
    })

    const removeLine = wrapper.findAll('button').find((button) =>
      button.text().trim() === '删除',
    )
    expect(removeLine).toBeDefined()
    await removeLine!.trigger('click')
    await flushPromises()

    expect(deleteReimbursementDraftFile).not.toHaveBeenCalled()
    expect(drafts.files).toEqual([source])
    expect(expense.items).toEqual([])
    expect(expense.dismissedOcrFileIds).toEqual(['file-1'])
    expect(ElMessageBox.confirm).toHaveBeenCalledWith(
      expect.stringContaining('附件仍保留'),
      expect.any(String),
      expect.any(Object),
    )

    const adopt = wrapper.findAll('button').find((button) =>
      button.text().trim() === '添加到费用明细',
    )
    expect(adopt).toBeDefined()
    await adopt!.trigger('click')
    await flushPromises()

    expect(expense.items).toEqual([
      expect.objectContaining({ sourceFileId: 'file-1', amount: '10.00' }),
    ])
    expect(expense.dismissedOcrFileIds).toEqual([])

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
      button.text().trim() === '忽略此票据',
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
        button.text().trim() === '忽略此票据',
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
        receiptCount: 2,
      }),
    ])
    expect(drafts.files[0]?.ocrResult?.amount).toBe('20.00')
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
      button.text().trim() === '删除文件',
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
      button.text().trim() === '删除文件',
    )
    await remove!.trigger('click')
    await flushPromises()

    expect(drafts.files).toEqual([source])
    expect(expense.items).toEqual([
      expect.objectContaining({ id: 'ocr-file-1', amount: '10.00' }),
    ])
    expect(wrapper.text()).toContain('已同步草稿最新状态')

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
    expect(drafts.files[0]?.ocrResult?.amount).toBe('20.00')
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('未自动新增或覆盖'))
    expect(wrapper.findAll('button').some((button) =>
      button.text().trim() === '添加到费用明细',
    )).toBe(true)

    wrapper.unmount()
  })
})
