<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { getReimbursementFileContent } from '@/api/reimbursements'
import { apiErrorMessage } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'
import { useExpenseStore } from '@/stores/expense'
import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'
import type { ExpenseCategoryId, ExpenseItem } from '@/types/expenses'
import { isForeignExpense } from '@/types/expenses'
import type { ReceiptFileState, ReceiptFileStatus } from '@/types/receipts'
import type {
  ReimbursementDraftFile,
  ReimbursementDraftFileRole,
} from '@/types/reimbursements'
import { formatFileSize } from '@/utils/receiptFiles'

const props = withDefaults(defineProps<{
  durable?: boolean
  readonly?: boolean
}>(), {
  durable: false,
  readonly: false,
})

const expense = useExpenseStore()
const drafts = useReimbursementDraftStore()
const auth = useAuthStore()
const receiptInput = ref<HTMLInputElement | null>(null)
const durableExpenseInput = ref<HTMLInputElement | null>(null)
const durableAttachmentInput = ref<HTMLInputElement | null>(null)
const durableOperating = ref(false)
const durableErrors = reactive<Record<string, string>>({})
let durableOperationGeneration = 0
let durableUnmounted = false
let activeDurableOperation: DurableOperationScope | null = null
const receiptPreviewVisible = ref(false)
const receiptPreviewUrl = ref('')
const receiptPreviewName = ref('')
const receiptPreviewKind = ref<'image' | 'pdf'>('image')
const previewLoading = ref(false)
let previewController: AbortController | null = null
const editorVisible = ref(false)
const editorRevision = ref(0)
const editor = reactive({
  id: '',
  category: '' as ExpenseCategoryId,
  date: '',
  description: '',
  amount: '',
  receiptCount: 1,
  requiresItinerary: false,
  itineraryFileIds: [] as string[],
  originalCurrency: '',
  originalAmount: '',
  cnyAmountConfirmed: false,
})

const receiptStatusLabels: Record<ReceiptFileStatus, string> = {
  queued: '等待上传',
  uploading: '上传中',
  uploaded: '已上传',
  recognizing: '本地识别中',
  recognized: '识别完成',
  done: 'OCR 已填入',
  failed: '需要处理',
}
const warningLabels: Record<string, string> = {
  MANUAL_REVIEW_REQUIRED: '需人工核对',
  MISSING_AMOUNT: '缺少金额',
  MISSING_DATE: '缺少日期',
  MISSING_DESCRIPTION: '缺少说明',
  MISSING_ROUTE: '缺少行程路线',
  LOW_CONFIDENCE: '识别置信度较低',
  LOW_OCR_CONFIDENCE: '识别置信度较低',
  QR_AMOUNT_REQUIRES_REVIEW: '金额来自二维码，请核对价税合计',
  QR_AMOUNT_MISMATCH: '二维码金额与票面金额不一致',
  QR_ISSUE_DATE_USED: '日期来自二维码开票日期，请核对发生日期',
  INVOICE_DATE_USED_AS_OCCURRENCE: '未识别到发生日期，当前使用开票日期',
  FOREIGN_CURRENCY_REQUIRES_CONFIRMATION: '请确认人民币报销金额',
  FOREIGN_CURRENCY_REQUIRES_CNY_AMOUNT: '请确认原票币种和人民币报销金额',
  MISSING_ITINERARY: '请补充对应行程单',
}
const durableRoleLabels: Record<ReimbursementDraftFileRole, string> = {
  EXPENSE_SOURCE: '票据/发票',
  ATTACHMENT_ONLY: '行程单/证明材料',
}

const categoryNames = computed<Record<string, string>>(() =>
  Object.fromEntries(expense.categories.map((item) => [item.id, item.name])),
)
const unlinkedReceiptFiles = computed(() =>
  expense.receiptFiles.filter((receipt) => !receipt.ocrItemId),
)
const durableFiles = computed(() => drafts.files.filter((file) => file.status !== 'PURGED'))
const unlinkedDurableFiles = computed(() => durableFiles.value.filter((file) =>
  !expense.items.some((item) => item.sourceFileId === file.id || item.itineraryFileIds?.includes(file.id)),
))
const itineraryOptions = computed(() => durableFiles.value.filter((file) =>
  file.status === 'ACTIVE' && file.role === 'ATTACHMENT_ONLY',
))
const foreignEditor = computed(() => Boolean(editor.originalCurrency && editor.originalCurrency.toUpperCase() !== 'CNY')
  || expense.items.find((item) => item.id === editor.id)?.requiresCnyConfirmation
  || editorSource.value?.ocrResult?.type === 'foreign_receipt'
  || editorSource.value?.ocrResult?.warnings.includes('FOREIGN_CURRENCY_REQUIRES_CNY_AMOUNT'))
const editorSource = computed(() => durableFiles.value.find((file) =>
  file.id === expense.items.find((item) => item.id === editor.id)?.sourceFileId,
))
const sourceRequiresItinerary = computed(() => editorSource.value?.ocrResult?.requiresItinerary
  || editorSource.value?.ocrResult?.transportType === 'ride_hailing')
const unresolvedDurableOcrFiles = computed(() => durableFiles.value.filter(
  (file) => isUnresolvedDurableOcrFile(file),
))
const durableBusy = computed(() => durableOperating.value || drafts.busy)
const durableMutationDisabledReason = computed(() => {
  if (props.readonly) return '当前操作进行中，费用明细和附件暂不可修改'
  const current = drafts.currentDraft
  if (!current) return '正在准备报销表单'
  if (current.status === 'DRAFT' || current.status === 'REVIEW_READY') return ''
  if (current.status === 'LOCKED') return '当前报销已提交，不能再修改附件'
  if (current.status === 'EXPIRED') return '当前报销已过期，不能再修改附件'
  return '当前报销不能再修改附件'
})
const durableActionDisabledReason = computed(() => durableMutationDisabledReason.value
  || (durableBusy.value ? '请等待当前文件操作完成' : ''))
const canAddExpenseItem = computed(
  () =>
    !props.readonly
    && expense.items.length < expense.maxExpenseItems
    && expense.manualCategories.length > 0
    && !expense.categoryLoadError,
)
const receiptUploadDisabledReason = computed(() => {
  if (props.readonly) return '当前操作进行中，费用明细和附件暂不可修改'
  if (props.durable) {
    if (durableActionDisabledReason.value) return durableActionDisabledReason.value
    if (durableFiles.value.length >= expense.receiptUploadLimits.maxFiles) {
      return `本次报销已达到 ${expense.receiptUploadLimits.maxFiles} 个附件上限`
    }
    return ''
  }
  if (expense.receiptBusy) return '请等待当前票据处理完成'
  if (
    expense.receiptFiles.filter((file) => file.tempId).length
      >= expense.receiptUploadLimits.maxFiles
  ) {
    return `当前会话已达到 ${expense.receiptUploadLimits.maxFiles} 个临时票据上限`
  }
  return ''
})
const newItemDisabledReason = computed(() => {
  if (props.readonly) return '当前操作进行中，费用明细暂不可修改'
  if (expense.items.length >= expense.maxExpenseItems) {
    return `费用明细已达到 ${expense.maxExpenseItems} 条上限`
  }
  if (expense.categoriesLoading) return '费用类别正在加载'
  if (expense.categoryLoadError) return expense.categoryLoadError
  if (!expense.manualCategories.length) return '暂无可手工选择的费用类别'
  return ''
})

interface DurableOperationScope {
  draftId: string
  departmentId: string
  generation: number
}

onBeforeUnmount(() => {
  durableUnmounted = true
  durableOperationGeneration += 1
  activeDurableOperation = null
  releaseReceiptPreview()
  previewController?.abort()
})

watch(
  () => [
    drafts.currentDraft?.id,
    drafts.currentDraft?.status,
    auth.session?.selectedDepartment?.id,
    props.readonly,
  ] as const,
  () => {
    const active = activeDurableOperation
    if (
      !active
      || (
        drafts.currentDraft?.id === active.draftId
        && (auth.session?.selectedDepartment?.id ?? '') === active.departmentId
        && isDurableDraftEditable()
      )
    ) return
    durableOperationGeneration += 1
    activeDurableOperation = null
    durableOperating.value = false
  },
  { flush: 'sync' },
)

function beginDurableOperation(): DurableOperationScope | null {
  const current = drafts.currentDraft
  if (durableUnmounted || !current || !isDurableDraftEditable()) return null
  const scope = {
    draftId: current.id,
    departmentId: auth.session?.selectedDepartment?.id ?? '',
    generation: ++durableOperationGeneration,
  }
  activeDurableOperation = scope
  return scope
}

function acceptsDurableOperation(scope: DurableOperationScope): boolean {
  return !durableUnmounted
    && scope.generation === durableOperationGeneration
    && drafts.currentDraft?.id === scope.draftId
    && (auth.session?.selectedDepartment?.id ?? '') === scope.departmentId
    && isDurableDraftEditable()
}

function isDurableDraftEditable(): boolean {
  return !props.readonly && (
    drafts.currentDraft?.status === 'DRAFT'
    || drafts.currentDraft?.status === 'REVIEW_READY'
  )
}

function finishDurableOperation(scope: DurableOperationScope): void {
  if (scope.generation !== durableOperationGeneration) return
  activeDurableOperation = null
  durableOperating.value = false
}

function openNewItem(): void {
  if (props.readonly) return
  const firstCategory = expense.manualCategories[0]
  if (!firstCategory) {
    ElMessage.error(expense.categoryLoadError || '费用类别尚未加载，请稍后重试')
    return
  }
  Object.assign(editor, {
    id: '',
    category: firstCategory.id,
    date: '',
    description: '',
    amount: '',
    receiptCount: 1,
    requiresItinerary: false,
    itineraryFileIds: [],
    originalCurrency: '',
    originalAmount: '',
    cnyAmountConfirmed: false,
  })
  editorRevision.value += 1
  editorVisible.value = true
}

function openEditItem(item: ExpenseItem): void {
  if (props.readonly) return
  Object.assign(editor, {
    id: item.id,
    category: item.category,
    date: item.date ?? '',
    description: item.description,
    amount: item.amount,
    receiptCount: item.receiptCount,
    requiresItinerary: item.requiresItinerary || item.transportType === 'ride_hailing' || false,
    itineraryFileIds: [...(item.itineraryFileIds ?? [])],
    originalCurrency: item.originalCurrency ?? '',
    originalAmount: item.originalAmount ?? '',
    cnyAmountConfirmed: item.cnyAmountConfirmed ?? false,
  })
  editorRevision.value += 1
  editorVisible.value = true
}

function receiptStatusType(status: ReceiptFileStatus): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'done') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'uploading' || status === 'recognizing' || status === 'recognized') return 'warning'
  return 'info'
}

function durableStatusLabel(file: ReimbursementDraftFile): string {
  if (file.status === 'RESERVED') return '等待上传'
  if (file.status === 'WRITING') return '上传中'
  if (file.status === 'FAILED') return '上传失败'
  if (file.status === 'DELETING') return '删除中'
  if (file.status === 'PURGED') return '已删除'
  if (file.role === 'ATTACHMENT_ONLY') return '已上传'
  if (file.ocrStatus === 'RUNNING') return '识别中'
  if (file.ocrStatus === 'COMPLETE') return '识别完成'
  if (file.ocrStatus === 'FAILED') return '识别失败'
  return '等待识别'
}

function durableStatusType(
  file: ReimbursementDraftFile,
): 'success' | 'warning' | 'danger' | 'info' {
  if (file.status === 'FAILED' || file.ocrStatus === 'FAILED') return 'danger'
  if (file.status !== 'ACTIVE' || file.ocrStatus === 'RUNNING') return 'warning'
  if (file.role === 'ATTACHMENT_ONLY' || file.ocrStatus === 'COMPLETE') return 'success'
  return 'info'
}

function durableFileByItemId(itemId: string): ReimbursementDraftFile | undefined {
  const sourceFileId = expense.items.find((item) => item.id === itemId)?.sourceFileId
  return sourceFileId
    ? durableFiles.value.find((file) => file.id === sourceFileId)
    : undefined
}

function itineraryFiles(item: ExpenseItem): ReimbursementDraftFile[] {
  return durableFiles.value.filter((file) => item.itineraryFileIds?.includes(file.id))
}

function needsItinerary(item: ExpenseItem): boolean {
  return Boolean(item.requiresItinerary || item.transportType === 'ride_hailing')
}

function durableFileError(file: ReimbursementDraftFile | undefined): string {
  if (!file) return ''
  return durableErrors[file.id]
    ?? file.ocrResult?.error?.message
    ?? ''
}

function durableOcrSummary(file: ReimbursementDraftFile): string {
  const result = file.ocrResult
  if (!result) return ''
  return [
    result.date,
    result.description?.trim(),
    result.amount ? `¥${result.amount}` : null,
  ].filter(Boolean).join(' · ')
}

function canRetryDurableRecognition(file: ReimbursementDraftFile | undefined): boolean {
  return Boolean(file
    && file.status === 'ACTIVE'
    && file.role === 'EXPENSE_SOURCE'
    && file.ocrStatus !== 'RUNNING')
}

function canAdoptDurableRecognition(file: ReimbursementDraftFile): boolean {
  return file.status === 'ACTIVE'
    && file.role === 'EXPENSE_SOURCE'
    && ['COMPLETE', 'FAILED'].includes(file.ocrStatus)
    && file.ocrResult !== null
    && !expense.items.some((item) => item.sourceFileId === file.id)
}

function isUnresolvedDurableOcrFile(file: ReimbursementDraftFile): boolean {
  return file.status === 'ACTIVE'
    && file.role === 'EXPENSE_SOURCE'
    && ['COMPLETE', 'FAILED'].includes(file.ocrStatus)
    && !expense.items.some((item) => item.sourceFileId === file.id)
    && !expense.dismissedOcrFileIds.includes(file.id)
}

function readableWarning(warning: string): string {
  return warningLabels[warning] ?? '请核对识别结果'
}

function chooseReceiptFiles(): void {
  if (props.readonly) return
  if (props.durable) durableExpenseInput.value?.click()
  else receiptInput.value?.click()
}

function chooseAttachmentFiles(): void {
  if (props.readonly) return
  durableAttachmentInput.value?.click()
}

function releaseReceiptPreview(): void {
  previewController?.abort()
  previewController = null
  if (receiptPreviewUrl.value) URL.revokeObjectURL(receiptPreviewUrl.value)
  receiptPreviewUrl.value = ''
}

function openReceiptPreview(receipt: ReceiptFileState): void {
  if (props.readonly) return
  releaseReceiptPreview()
  try {
    receiptPreviewName.value = receipt.name
    receiptPreviewKind.value =
      receipt.file.type === 'application/pdf' || receipt.name.toLowerCase().endsWith('.pdf')
        ? 'pdf'
        : 'image'
    receiptPreviewUrl.value = URL.createObjectURL(receipt.file)
    receiptPreviewVisible.value = true
  } catch {
    ElMessage.error('无法预览该票据，请重新选择文件')
  }
}

async function previewDurableFile(file: ReimbursementDraftFile | undefined): Promise<void> {
  const draft = drafts.currentDraft
  if (!draft || !file || file.status !== 'ACTIVE') return
  releaseReceiptPreview()
  const controller = new AbortController()
  previewController = controller
  previewLoading.value = true
  try {
    const blob = await getReimbursementFileContent(draft.id, file.id, { signal: controller.signal })
    if (controller.signal.aborted || drafts.currentDraft?.id !== draft.id || durableUnmounted) return
    receiptPreviewName.value = file.name
    receiptPreviewKind.value = file.mediaType === 'application/pdf' ? 'pdf' : 'image'
    receiptPreviewUrl.value = URL.createObjectURL(blob)
    receiptPreviewVisible.value = true
  } catch (error) {
    if (!controller.signal.aborted) ElMessage.error(apiErrorMessage(error, '材料预览失败，请重试'))
  } finally {
    if (previewController === controller) previewLoading.value = false
  }
}

async function previewItemReceipt(itemId: string): Promise<void> {
  if (props.durable) { await previewDurableFile(durableFileByItemId(itemId)); return }
  const receipt = expense.receiptByItemId(itemId)
  if (receipt) openReceiptPreview(receipt)
}

async function onReceiptSelection(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const files = [...(input.files ?? [])]
  input.value = ''
  if (props.readonly || !files.length) return
  const result = await expense.addReceiptFiles(files)
  for (const rejection of result.rejected.slice(0, 3)) {
    ElMessage.warning(`${rejection.file.name}：${rejection.message}`)
  }
  if (result.rejected.length > 3) {
    ElMessage.warning(`另有 ${result.rejected.length - 3} 个文件未加入，请检查数量和大小`)
  }
}

function durableTripYear(): number | undefined {
  return expense.includeSubsidy && /^\d{4}-/.test(expense.trip.startDate)
    ? Number(expense.trip.startDate.slice(0, 4))
    : undefined
}

function expenseItemSnapshot(item: ExpenseItem | undefined): string | null {
  if (!item) return null
  // Protect every employee-editable field, including proof links and foreign
  // currency confirmation, from a recognition response that arrives later.
  return JSON.stringify(item)
}

function readableOperationError(error: unknown, fallback: string): string {
  return drafts.mutationError
    || (error instanceof Error && error.message ? error.message : fallback)
}

async function recognizeDurableFile(
  file: ReimbursementDraftFile,
  scope: DurableOperationScope,
  upsertItem: boolean,
): Promise<ReimbursementDraftFile | null> {
  if (!acceptsDurableOperation(scope)) return null
  const result = await drafts.recognizeFile(file.id, durableTripYear())
  if (!acceptsDurableOperation(scope) || result.draftId !== scope.draftId) return null
  delete durableErrors[file.id]
  if (upsertItem && !expense.upsertDraftOcrItem(result.file)) {
    durableErrors[file.id] = '识别结果不完整，请重试或手工添加费用明细'
  }
  if (upsertItem) void expense.refreshCalculations()
  return result.file
}

async function onDurableSelection(
  event: Event,
  role: ReimbursementDraftFileRole,
): Promise<void> {
  const input = event.target as HTMLInputElement
  const files = [...(input.files ?? [])]
  input.value = ''
  if (props.readonly || !props.durable || !files.length || durableBusy.value) return
  const scope = beginDurableOperation()
  if (!scope) return

  durableOperating.value = true
  try {
    for (const file of files) {
      if (!acceptsDurableOperation(scope)) break
      try {
        const uploaded = await drafts.uploadFile(file, role)
        if (!acceptsDurableOperation(scope) || uploaded.draftId !== scope.draftId) break
        delete durableErrors[uploaded.file.id]
        if (
          role === 'EXPENSE_SOURCE'
          && await recognizeDurableFile(uploaded.file, scope, true) === null
        ) break
      } catch (error) {
        if (!acceptsDurableOperation(scope)) break
        ElMessage.error(`${file.name}：${readableOperationError(error, '文件处理失败，请重试')}`)
      }
    }
  } finally {
    finishDurableOperation(scope)
  }
}

async function retryDurableRecognition(file: ReimbursementDraftFile): Promise<void> {
  if (props.readonly || durableBusy.value || !canRetryDurableRecognition(file)) return
  const scope = beginDurableOperation()
  if (!scope) return
  const linkedItemSnapshot = expenseItemSnapshot(
    expense.items.find((item) => item.sourceFileId === file.id),
  )
  durableOperating.value = true
  try {
    const recognized = await recognizeDurableFile(file, scope, false)
    if (!recognized) return
    if (
      linkedItemSnapshot === null
      || expenseItemSnapshot(expense.items.find((item) => item.sourceFileId === file.id))
        !== linkedItemSnapshot
    ) {
      ElMessage.warning('OCR 结果已更新；现有费用明细可能已人工修改，未自动新增或覆盖')
      return
    }
    if (!expense.upsertDraftOcrItem(recognized)) {
      durableErrors[file.id] = '识别结果不完整，请重试或手工添加费用明细'
      return
    }
    void expense.refreshCalculations()
    if (!durableFileError(recognized)) ElMessage.success('已用新的 OCR 结果更新明细')
  } catch (error) {
    if (!acceptsDurableOperation(scope)) return
    durableErrors[file.id] = readableOperationError(error, '票据识别失败，请重试')
  } finally {
    finishDurableOperation(scope)
  }
}

async function removeDurableFile(file: ReimbursementDraftFile): Promise<void> {
  if (props.readonly || durableBusy.value || !['ACTIVE', 'DELETING'].includes(file.status)) return
  const scope = beginDurableOperation()
  if (!scope) return
  const linked = expense.items.some((item) => item.sourceFileId === file.id)
  durableOperating.value = true
  try {
    if (linked && file.status === 'ACTIVE') {
      try {
        await ElMessageBox.confirm(
          '该票据已关联一条费用明细。删除文件会同时移除当前费用明细，是否继续？',
          '确认删除票据',
          {
            confirmButtonText: '删除',
            cancelButtonText: '取消',
            type: 'warning',
          },
        )
      } catch {
        return
      }
    }
    if (!acceptsDurableOperation(scope)) return
    const result = await drafts.removeFile(file.id)
    if (
      !acceptsDurableOperation(scope)
      || result.draftId !== scope.draftId
      || result.deletedFileId !== file.id
    ) return
    expense.removeDraftFileAssociation(file.id)
    delete durableErrors[file.id]
    ElMessage.success('已从本次报销移除该文件')
    void expense.refreshCalculations()
  } catch (error) {
    if (!acceptsDurableOperation(scope)) return
    const stillExists = drafts.files.some((current) => current.id === file.id)
    if (!stillExists) {
      expense.removeDraftFileAssociation(file.id)
      delete durableErrors[file.id]
      drafts.mutationError = ''
      ElMessage.success('服务端已确认文件删除，当前费用明细已同步移除')
      void expense.refreshCalculations()
      return
    }
    durableErrors[file.id] = readableOperationError(error, '文件删除失败，请重试')
  } finally {
    finishDurableOperation(scope)
  }
}

function adoptDurableRecognition(file: ReimbursementDraftFile): void {
  if (
    props.readonly
    || durableActionDisabledReason.value
    || !canAdoptDurableRecognition(file)
  ) return
  if (!expense.upsertDraftOcrItem(file)) {
    durableErrors[file.id] = '识别结果不完整，请手工添加费用明细'
    return
  }
  delete durableErrors[file.id]
  ElMessage.success('已将识别结果添加到费用明细')
  void expense.refreshCalculations()
}

async function ignoreDurableRecognition(file: ReimbursementDraftFile): Promise<void> {
  if (
    props.readonly
    || durableActionDisabledReason.value
    || !isUnresolvedDurableOcrFile(file)
  ) return
  const scope = beginDurableOperation()
  if (!scope) return
  durableOperating.value = true
  try {
    try {
      await ElMessageBox.confirm(
        '忽略后该文件仍作为 OA 附件保留，但识别结果不计入费用金额。是否继续？',
        '忽略此票据',
        {
          confirmButtonText: '确认忽略',
          cancelButtonText: '返回检查',
          type: 'warning',
        },
      )
    } catch {
      return
    }
    if (!acceptsDurableOperation(scope)) return
    const current = drafts.files.find((candidate) => candidate.id === file.id)
    if (!current || !isUnresolvedDurableOcrFile(current)) return
    if (expense.dismissDraftOcrFile(file.id)) {
      ElMessage.success('已忽略该票据的识别结果，原文件仍会作为附件提交')
    }
  } finally {
    finishDurableOperation(scope)
  }
}

async function removeReceipt(localId: string): Promise<void> {
  if (props.readonly) return
  const receipt = expense.receiptFiles.find((entry) => entry.localId === localId)
  if (!receipt) return
  if (receipt.ocrItemId) {
    try {
      await ElMessageBox.confirm(
        '删除这条 OCR 明细时会同时移除对应的临时票据。',
        '确认删除明细',
        {
          confirmButtonText: '移除',
          cancelButtonText: '取消',
          type: 'warning',
        },
      )
    } catch {
      return
    }
  }
  if (await expense.removeReceipt(localId)) {
    ElMessage.success('已从当前报销单移除该票据')
  }
}

function saveItem(): void {
  if (props.readonly) return
  try {
    if (editor.originalCurrency.trim() && !/^[A-Z]{3}$/.test(editor.originalCurrency.trim().toUpperCase())) {
      throw new Error('请填写票面原币币种，例如 VND、USD 或 EUR')
    }
    expense.upsertManualItem({
      id: editor.id || undefined,
      category: editor.category,
      date: editor.date,
      displayDate: editor.date,
      description: editor.description,
      amount: editor.amount,
      receiptCount: editor.receiptCount,
      requiresItinerary: editor.requiresItinerary || Boolean(sourceRequiresItinerary.value),
      transportType: editor.requiresItinerary ? 'ride_hailing'
        : editorSource.value?.ocrResult?.transportType ?? 'other',
      itineraryFileIds: [...editor.itineraryFileIds],
      originalCurrency: editor.originalCurrency.trim().toUpperCase() || undefined,
      originalAmount: editor.originalAmount.trim() || undefined,
      cnyAmountConfirmed: foreignEditor.value && editor.cnyAmountConfirmed,
      requiresCnyConfirmation: Boolean(foreignEditor.value),
      warnings: [],
    })
    editorVisible.value = false
    void expense.refreshCalculations()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '费用明细填写不正确')
  }
}

async function removeItem(id: string): Promise<void> {
  if (props.readonly) return
  if (props.durable) {
    const item = expense.items.find((entry) => entry.id === id)
    const file = durableFileByItemId(id)
    if (item?.sourceFileId && file) {
      await removeDurableFile(file)
    } else {
      expense.removeItem(id)
      void expense.refreshCalculations()
    }
    return
  }
  const receipt = expense.receiptByItemId(id)
  if (receipt) {
    await removeReceipt(receipt.localId)
    return
  }
  expense.removeItem(id)
  void expense.refreshCalculations()
}

async function retryItemRecognition(id: string): Promise<void> {
  if (props.readonly) return
  if (props.durable) {
    const file = durableFileByItemId(id)
    if (file) await retryDurableRecognition(file)
    return
  }
  const receipt = expense.receiptByItemId(id)
  if (!receipt) return
  await expense.retryReceipt(receipt.localId)
  if (receipt.status === 'done' && !receipt.error) ElMessage.success('已用新的 OCR 结果更新明细')
  void expense.refreshCalculations()
}
</script>

<template>
  <el-card
    shadow="never"
    class="content-card reimbursement-card"
  >
    <template #header>
      <div class="card-header">
        <div>
          <strong>费用明细</strong>
          <span class="section-note">OCR 结果会直接填入，发现不准确时直接编辑</span>
        </div>
        <div class="receipt-header-actions">
          <el-button
            :loading="expense.categoriesLoading"
            :disabled="!canAddExpenseItem"
            :title="newItemDisabledReason"
            @click="openNewItem"
          >
            手动添加
          </el-button>
          <el-button
            type="primary"
            :loading="props.durable ? durableBusy : expense.receiptBusy"
            :disabled="Boolean(receiptUploadDisabledReason)"
            :title="receiptUploadDisabledReason"
            @click="chooseReceiptFiles"
          >
            {{ props.durable ? '选择票据/发票' : '选择票据文件' }}
          </el-button>
          <el-button
            v-if="props.durable"
            :loading="durableBusy"
            :disabled="Boolean(receiptUploadDisabledReason)"
            :title="receiptUploadDisabledReason"
            @click="chooseAttachmentFiles"
          >
            添加行程单 / 证明材料
          </el-button>
        </div>
        <input
          v-if="!props.durable"
          ref="receiptInput"
          class="visually-hidden"
          type="file"
          accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
          multiple
          :disabled="props.readonly || expense.receiptBusy"
          @change="onReceiptSelection"
        >
        <input
          v-else
          ref="durableExpenseInput"
          data-testid="durable-expense-input"
          class="visually-hidden"
          type="file"
          accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
          multiple
          :disabled="Boolean(durableActionDisabledReason)"
          @change="onDurableSelection($event, 'EXPENSE_SOURCE')"
        >
        <input
          v-if="props.durable"
          ref="durableAttachmentInput"
          data-testid="durable-attachment-input"
          class="visually-hidden"
          type="file"
          accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
          multiple
          :disabled="Boolean(durableActionDisabledReason)"
          @change="onDurableSelection($event, 'ATTACHMENT_ONLY')"
        >
        <p
          v-if="receiptUploadDisabledReason || newItemDisabledReason"
          class="field-help action-help"
          role="status"
        >
          {{ receiptUploadDisabledReason || newItemDisabledReason }}
        </p>
      </div>
    </template>
    <el-alert
      :title="props.durable ? '票据自动识别，其他材料作为附件保存' : '一个文件只能放一张票据'"
      :description="props.durable
        ? '发票上传后自动填入下方明细；行程单请使用“添加行程单 / 证明材料”，并在对应费用的编辑窗口中选择。提交时按费用顺序汇总为一个 PDF。'
        : '支持一次选择多个 JPG、JPEG、PNG 和单页 PDF。OCR 结果会直接成为可编辑的费用条目；不支持多页汇总 PDF、行程单或文件合并。临时文件由后台自动清理。'"
      type="info"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="expense.ocrUnavailable"
      title="本地 OCR 当前不可用"
      description="对应条目已经保留，请直接编辑补充票据信息。系统不会转用付费或云端 OCR。"
      type="warning"
      :closable="false"
      show-icon
      class="receipt-alert"
    />
    <el-alert
      v-if="props.durable && drafts.mutationError"
      :title="drafts.mutationError"
      type="error"
      :closable="false"
      show-icon
      class="receipt-alert"
    />
    <el-alert
      v-if="props.durable && unresolvedDurableOcrFiles.length > 0"
      :title="`${unresolvedDurableOcrFiles.length} 张票据的 OCR 结果待确认`"
      description="请逐张选择“添加到费用明细”或“仅作为材料保留”，处理完成后即可提交。"
      type="warning"
      :closable="false"
      show-icon
      class="receipt-alert"
    />
    <div
      v-if="props.durable && unlinkedDurableFiles.length"
      class="receipt-list"
      aria-live="polite"
      aria-label="待处理票据和未关联材料"
    >
      <article
        v-for="file in unlinkedDurableFiles"
        :key="file.id"
        class="receipt-row"
        :aria-label="`${file.name}：${durableStatusLabel(file)}`"
      >
        <div class="receipt-main">
          <div class="receipt-name-line">
            <button
              type="button"
              class="receipt-file-preview-link"
              :disabled="file.status !== 'ACTIVE' || previewLoading"
              :aria-label="`预览材料 ${file.name}`"
              @click="previewDurableFile(file)"
            >
              {{ file.name }} · 预览
            </button>
            <el-tag size="small">
              {{ durableRoleLabels[file.role] }}
            </el-tag>
            <el-tag
              size="small"
              :type="durableStatusType(file)"
            >
              {{ durableStatusLabel(file) }}
            </el-tag>
          </div>
          <span class="receipt-meta">
            {{ formatFileSize(file.sizeBytes) }}
            <template v-if="file.role === 'ATTACHMENT_ONLY'"> · 尚未关联费用，可在对应费用的编辑窗口中选择</template>
          </span>
          <p
            v-if="durableOcrSummary(file)"
            class="receipt-meta"
          >
            OCR：{{ durableOcrSummary(file) }}
          </p>
          <p
            v-if="durableFileError(file)"
            class="field-error receipt-error"
            role="alert"
          >
            {{ durableFileError(file) }}
          </p>
        </div>
        <div class="receipt-actions">
          <el-button
            v-if="canAdoptDurableRecognition(file)"
            link
            type="primary"
            :disabled="Boolean(durableActionDisabledReason)"
            :title="durableActionDisabledReason"
            @click="adoptDurableRecognition(file)"
          >
            添加到费用明细
          </el-button>
          <el-button
            v-if="isUnresolvedDurableOcrFile(file)"
            link
            type="warning"
            :disabled="Boolean(durableActionDisabledReason)"
            :title="durableActionDisabledReason"
            @click="ignoreDurableRecognition(file)"
          >
            仅作为材料保留
          </el-button>
          <el-button
            v-if="canRetryDurableRecognition(file)"
            link
            type="primary"
            :disabled="Boolean(durableActionDisabledReason)"
            :title="durableActionDisabledReason"
            @click="retryDurableRecognition(file)"
          >
            重新识别
          </el-button>
          <el-button
            link
            type="danger"
            :disabled="Boolean(durableActionDisabledReason)
              || !['ACTIVE', 'DELETING'].includes(file.status)"
            :title="durableActionDisabledReason"
            @click="removeDurableFile(file)"
          >
            {{ file.status === 'DELETING' ? '重试删除' : '删除文件' }}
          </el-button>
        </div>
      </article>
    </div>
    <div
      v-if="!props.durable && unlinkedReceiptFiles.length"
      class="receipt-list"
      aria-live="polite"
      aria-label="票据处理进度"
    >
      <article
        v-for="receipt in unlinkedReceiptFiles"
        :key="receipt.localId"
        class="receipt-row"
        :aria-label="`${receipt.name}：${receiptStatusLabels[receipt.status]}`"
      >
        <div class="receipt-main">
          <div class="receipt-name-line">
            <button
              type="button"
              class="receipt-file-preview-link"
              :disabled="props.readonly"
              :aria-label="`预览票据 ${receipt.name}`"
              @click="openReceiptPreview(receipt)"
            >
              {{ receipt.name }} · 预览
            </button>
            <el-tag
              size="small"
              :type="receiptStatusType(receipt.status)"
            >
              {{ receiptStatusLabels[receipt.status] }}
            </el-tag>
          </div>
          <span class="receipt-meta">{{ formatFileSize(receipt.size) }}</span>
          <el-progress
            v-if="receipt.status === 'uploading'"
            :percentage="receipt.uploadProgress"
            :stroke-width="6"
            :aria-label="`${receipt.name} 上传进度 ${receipt.uploadProgress}%`"
          />
          <p
            v-if="receipt.error"
            class="field-error receipt-error"
            role="alert"
          >
            {{ receipt.error }}
          </p>
        </div>
        <div class="receipt-actions">
          <el-button
            v-if="receipt.tempId && receipt.status === 'failed'"
            link
            type="primary"
            :disabled="props.readonly || expense.receiptBusy"
            @click="expense.retryReceipt(receipt.localId)"
          >
            重新识别
          </el-button>
          <el-button
            v-else-if="receipt.status === 'failed'"
            link
            type="primary"
            :disabled="props.readonly || expense.receiptBusy"
            @click="expense.retryReceipt(receipt.localId)"
          >
            重试上传
          </el-button>
          <el-button
            link
            type="danger"
            :disabled="props.readonly || expense.receiptBusy"
            title="从当前报销单移除"
            @click="removeReceipt(receipt.localId)"
          >
            移除
          </el-button>
        </div>
      </article>
    </div>
    <div
      v-if="expense.categoryLoadError"
      class="category-load-error"
    >
      <el-alert
        :title="expense.categoryLoadError"
        type="error"
        show-icon
        :closable="false"
      />
      <el-button
        :loading="expense.categoriesLoading"
        :disabled="props.readonly"
        @click="expense.loadCategories(true)"
      >
        重新加载费用类别
      </el-button>
    </div>
    <el-empty
      v-if="expense.items.length === 0
        && (props.durable ? durableFiles.length === 0 : unlinkedReceiptFiles.length === 0)"
      description="还没有费用明细"
      :image-size="80"
    />
    <el-table
      v-else
      :data="expense.sortedItems"
      class="expense-table"
    >
      <el-table-column
        label="类型"
        min-width="120"
      >
        <template #default="scope">
          <div class="expense-category-cell">
            <span>{{ categoryNames[scope.row.category] ?? scope.row.category }}</span>
            <el-tag
              v-if="scope.row.source === 'ocr'"
              size="small"
              type="info"
            >
              OCR
            </el-tag>
            <button
              v-if="!props.durable && expense.receiptByItemId(scope.row.id)?.name"
              type="button"
              class="receipt-file-preview-link receipt-meta"
              :disabled="props.readonly"
              :aria-label="`预览票据 ${expense.receiptByItemId(scope.row.id)?.name}`"
              @click="previewItemReceipt(scope.row.id)"
            >
              {{ expense.receiptByItemId(scope.row.id)?.name }} · 预览
            </button>
            <button
              v-if="props.durable && durableFileByItemId(scope.row.id)?.name"
              type="button"
              class="receipt-file-preview-link receipt-meta"
              :disabled="previewLoading"
              :aria-label="`预览票据 ${durableFileByItemId(scope.row.id)?.name}`"
              @click="previewItemReceipt(scope.row.id)"
            >
              {{ durableFileByItemId(scope.row.id)?.name }} · 预览
            </button>
            <button
              v-for="file in itineraryFiles(scope.row)"
              :key="file.id"
              type="button"
              class="receipt-file-preview-link receipt-meta"
              :disabled="previewLoading"
              @click="previewDurableFile(file)"
            >
              行程单：{{ file.name }} · 预览
            </button>
            <span
              v-if="needsItinerary(scope.row) && itineraryFiles(scope.row).length === 0"
              class="field-error"
            >缺少行程单，请编辑补齐</span>
            <span
              v-if="scope.row.warnings?.length"
              class="ocr-warning"
            >
              {{ scope.row.warnings.map(readableWarning).join('、') }}
            </span>
            <span
              v-if="props.durable && durableFileError(durableFileByItemId(scope.row.id))"
              class="field-error"
            >
              {{ durableFileError(durableFileByItemId(scope.row.id)) }}
            </span>
            <span
              v-else-if="expense.receiptByItemId(scope.row.id)?.error"
              class="field-error"
            >
              {{ expense.receiptByItemId(scope.row.id)?.error }}
            </span>
          </div>
        </template>
      </el-table-column>
      <el-table-column
        label="日期"
        min-width="120"
      >
        <template #default="scope">
          {{ scope.row.displayDate || '待补充' }}
        </template>
      </el-table-column>
      <el-table-column
        prop="description"
        label="说明"
        min-width="180"
      />
      <el-table-column
        label="金额"
        width="110"
        align="right"
      >
        <template #default="scope">
          {{ scope.row.amount ? `¥${scope.row.amount}` : '待补充' }}
          <div
            v-if="isForeignExpense(scope.row)"
            class="receipt-meta"
          >
            原币 {{ scope.row.originalAmount || '待补充' }} {{ scope.row.originalCurrency || '币种待确认' }}
            <span
              v-if="!scope.row.cnyAmountConfirmed"
              class="field-error"
            >请确认人民币金额</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column
        prop="receiptCount"
        label="张数"
        width="72"
        align="center"
      />
      <el-table-column
        label="操作"
        width="190"
        fixed="right"
      >
        <template #default="scope">
          <el-button
            link
            type="primary"
            :disabled="props.readonly"
            @click="openEditItem(scope.row)"
          >
            编辑
          </el-button>
          <el-button
            v-if="props.durable
              ? Boolean(durableFileByItemId(scope.row.id)
                && canRetryDurableRecognition(durableFileByItemId(scope.row.id)))
              : Boolean(expense.receiptByItemId(scope.row.id)?.tempId)"
            link
            type="primary"
            :disabled="props.readonly || (props.durable
              ? Boolean(durableActionDisabledReason)
              : expense.receiptBusy)"
            :title="props.durable ? durableActionDisabledReason : ''"
            @click="retryItemRecognition(scope.row.id)"
          >
            重新识别
          </el-button>
          <el-button
            link
            type="danger"
            :disabled="props.readonly || (props.durable
              && Boolean(durableFileByItemId(scope.row.id))
              && Boolean(durableActionDisabledReason))"
            :title="props.durable && durableFileByItemId(scope.row.id)
              ? durableActionDisabledReason
              : ''"
            @click="removeItem(scope.row.id)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="expense-mobile-list">
      <article
        v-for="item in expense.sortedItems"
        :key="item.id"
        class="expense-mobile-card"
      >
        <div>
          <strong>{{ categoryNames[item.category] ?? item.category }}</strong>
          <span>{{ item.amount ? `¥${item.amount}` : '金额待补充' }}</span>
        </div>
        <p>{{ item.displayDate || '日期待补充' }} · {{ item.receiptCount }} 张</p>
        <p>{{ item.description }}</p>
        <p
          v-if="item.source === 'ocr'"
          class="ocr-warning"
        >
          OCR<template v-if="item.warnings?.length">
            · {{ item.warnings.map(readableWarning).join('、') }}
          </template>
        </p>
        <p v-if="!props.durable && expense.receiptByItemId(item.id)?.name">
          <button
            type="button"
            class="receipt-file-preview-link"
            :disabled="props.readonly"
            :aria-label="`预览票据 ${expense.receiptByItemId(item.id)?.name}`"
            @click="previewItemReceipt(item.id)"
          >
            {{ expense.receiptByItemId(item.id)?.name }} · 预览
          </button>
        </p>
        <p
          v-if="props.durable && durableFileByItemId(item.id)?.name"
          class="receipt-meta"
        >
          <button
            type="button"
            class="receipt-file-preview-link"
            :disabled="previewLoading"
            @click="previewItemReceipt(item.id)"
          >
            {{ durableFileByItemId(item.id)?.name }} · 预览
          </button>
        </p>
        <p
          v-for="file in itineraryFiles(item)"
          :key="file.id"
          class="receipt-meta"
        >
          <button
            type="button"
            class="receipt-file-preview-link"
            :disabled="previewLoading"
            @click="previewDurableFile(file)"
          >
            行程单：{{ file.name }} · 预览
          </button>
        </p>
        <p
          v-if="needsItinerary(item) && itineraryFiles(item).length === 0"
          class="field-error"
        >
          缺少行程单，请编辑补齐
        </p>
        <p
          v-if="isForeignExpense(item)"
          class="receipt-meta"
        >
          原币 {{ item.originalAmount || '待补充' }} {{ item.originalCurrency || '币种待确认' }}
          <span
            v-if="!item.cnyAmountConfirmed"
            class="field-error"
          >请确认人民币金额</span>
        </p>
        <p
          v-if="props.durable && durableFileError(durableFileByItemId(item.id))"
          class="field-error"
        >
          {{ durableFileError(durableFileByItemId(item.id)) }}
        </p>
        <p
          v-else-if="expense.receiptByItemId(item.id)?.error"
          class="field-error"
        >
          {{ expense.receiptByItemId(item.id)?.error }}
        </p>
        <div class="mobile-actions">
          <el-button
            size="small"
            :disabled="props.readonly"
            @click="openEditItem(item)"
          >
            编辑
          </el-button>
          <el-button
            v-if="props.durable
              ? canRetryDurableRecognition(durableFileByItemId(item.id))
              : Boolean(expense.receiptByItemId(item.id)?.tempId)"
            size="small"
            :disabled="props.readonly || (props.durable
              ? Boolean(durableActionDisabledReason)
              : expense.receiptBusy)"
            :title="props.durable ? durableActionDisabledReason : ''"
            @click="retryItemRecognition(item.id)"
          >
            重新识别
          </el-button>
          <el-button
            size="small"
            type="danger"
            plain
            :disabled="props.readonly || (props.durable
              && Boolean(durableFileByItemId(item.id))
              && Boolean(durableActionDisabledReason))"
            :title="props.durable && durableFileByItemId(item.id)
              ? durableActionDisabledReason
              : ''"
            @click="removeItem(item.id)"
          >
            删除
          </el-button>
        </div>
      </article>
    </div>
  </el-card>

  <el-dialog
    v-model="receiptPreviewVisible"
    :title="`票据预览：${receiptPreviewName}`"
    width="min(960px, calc(100% - 24px))"
    top="4vh"
    destroy-on-close
    @closed="releaseReceiptPreview"
  >
    <div class="receipt-preview-surface">
      <img
        v-if="receiptPreviewKind === 'image'"
        :src="receiptPreviewUrl"
        :alt="`${receiptPreviewName} 预览`"
      >
      <iframe
        v-else
        :src="receiptPreviewUrl"
        :title="`${receiptPreviewName} 预览`"
      />
    </div>
    <p class="field-help receipt-preview-help">
      <span>预览本次报销的原始材料，请核对金额、日期和票面内容。</span>
      <a
        v-if="receiptPreviewKind === 'pdf'"
        :href="receiptPreviewUrl"
        target="_blank"
        rel="noopener noreferrer"
      >PDF 未显示时在新窗口打开</a>
    </p>
    <template #footer>
      <el-button @click="receiptPreviewVisible = false">
        关闭
      </el-button>
    </template>
  </el-dialog>

  <el-dialog
    v-model="editorVisible"
    :title="editor.id ? '编辑费用明细' : '新增费用明细'"
    width="min(520px, calc(100% - 24px))"
  >
    <el-form
      label-position="top"
      :disabled="props.readonly"
    >
      <el-form-item label="费用类别">
        <el-select
          v-model="editor.category"
          class="full-width"
        >
          <el-option
            v-for="category in expense.manualCategories"
            :key="category.id"
            :label="category.name"
            :value="category.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="发生日期">
        <el-date-picker
          v-model="editor.date"
          type="date"
          value-format="YYYY-MM-DD"
          class="full-width"
        />
      </el-form-item>
      <el-form-item label="说明">
        <el-input
          v-model="editor.description"
          type="textarea"
          :rows="3"
          maxlength="500"
          show-word-limit
        />
      </el-form-item>
      <div class="trip-grid">
        <el-form-item :label="foreignEditor ? '人民币报销金额（元）' : '金额（元）'">
          <el-input
            v-model="editor.amount"
            inputmode="decimal"
            placeholder="0.00"
            maxlength="15"
            @input="editor.cnyAmountConfirmed = false"
          />
        </el-form-item>
        <el-form-item label="票据张数">
          <el-input-number
            :key="editorRevision"
            v-model="editor.receiptCount"
            :min="1"
            :max="10000"
            :step="1"
            step-strictly
            class="full-width"
          />
        </el-form-item>
      </div>
      <div class="trip-grid">
        <el-form-item label="原票币种（不确定时可留空）">
          <el-input
            v-model="editor.originalCurrency"
            placeholder="例如 VND、USD、EUR"
            maxlength="3"
            @input="editor.cnyAmountConfirmed = false"
          />
        </el-form-item>
        <el-form-item
          v-if="foreignEditor"
          label="原币金额"
        >
          <el-input
            v-model="editor.originalAmount"
            placeholder="例如 97600000"
            inputmode="decimal"
            @input="editor.cnyAmountConfirmed = false"
          />
        </el-form-item>
      </div>
      <el-form-item v-if="foreignEditor">
        <el-checkbox v-model="editor.cnyAmountConfirmed">
          已核对原币金额，并确认上述人民币报销金额
        </el-checkbox>
        <p class="field-help">
          人民币金额请按实际报销金额填写，系统不会把原币金额直接当作人民币。
        </p>
      </el-form-item>
      <template v-if="props.durable">
        <el-form-item>
          <el-checkbox
            v-model="editor.requiresItinerary"
            :disabled="Boolean(sourceRequiresItinerary)"
          >
            网约车费用（必须有对应行程单）
          </el-checkbox>
        </el-form-item>
        <el-form-item label="对应行程单 / 证明材料">
          <el-select
            v-model="editor.itineraryFileIds"
            multiple
            class="full-width"
            placeholder="选择已经上传的对应材料"
          >
            <el-option
              v-for="file in itineraryOptions"
              :key="file.id"
              :label="file.name"
              :value="file.id"
            />
          </el-select>
          <p class="field-help">
            先上传行程单，再确认对应关系。一份行程单包含多次行程时，可关联多条费用；汇总 PDF 中只保留一份。
          </p>
        </el-form-item>
      </template>
    </el-form>
    <template #footer>
      <el-button @click="editorVisible = false">
        取消
      </el-button>
      <el-button
        type="primary"
        :disabled="props.readonly"
        @click="saveItem"
      >
        保存
      </el-button>
    </template>
  </el-dialog>
</template>
