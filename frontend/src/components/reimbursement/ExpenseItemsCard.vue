<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { useAuthStore } from '@/stores/auth'
import { useExpenseStore } from '@/stores/expense'
import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'
import type { ExpenseCategoryId, ExpenseItem } from '@/types/expenses'
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
const editorVisible = ref(false)
const editorRevision = ref(0)
const editor = reactive({
  id: '',
  category: '' as ExpenseCategoryId,
  date: '',
  description: '',
  amount: '',
  receiptCount: 1,
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
}
const durableRoleLabels: Record<ReimbursementDraftFileRole, string> = {
  EXPENSE_SOURCE: '票据/发票',
  ATTACHMENT_ONLY: '其他附件',
}

const categoryNames = computed<Record<string, string>>(() =>
  Object.fromEntries(expense.categories.map((item) => [item.id, item.name])),
)
const unlinkedReceiptFiles = computed(() =>
  expense.receiptFiles.filter((receipt) => !receipt.ocrItemId),
)
const durableFiles = computed(() => drafts.files.filter((file) => file.status !== 'PURGED'))
const unresolvedDurableOcrFiles = computed(() => durableFiles.value.filter(
  (file) => isUnresolvedDurableOcrFile(file),
))
const durableBusy = computed(() => durableOperating.value || drafts.busy)
const durableMutationDisabledReason = computed(() => {
  if (props.readonly) return '当前操作进行中，费用明细和附件暂不可修改'
  const current = drafts.currentDraft
  if (!current) return '请先创建或打开一份报销草稿'
  if (current.status === 'DRAFT' || current.status === 'REVIEW_READY') return ''
  if (current.status === 'LOCKED') return '当前草稿已锁定，不能再修改附件'
  if (current.status === 'EXPIRED') return '当前草稿已过期，不能再修改附件'
  return '当前草稿不能再修改附件'
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
      return `当前草稿已达到 ${expense.receiptUploadLimits.maxFiles} 个附件上限`
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

function previewItemReceipt(itemId: string): void {
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
  return JSON.stringify({
    id: item.id,
    sourceFileId: item.sourceFileId ?? null,
    category: item.category,
    date: item.date ?? null,
    displayDate: item.displayDate,
    description: item.description,
    amount: item.amount,
    receiptCount: item.receiptCount,
    source: item.source,
    confidence: item.confidence ?? null,
    warnings: [...(item.warnings ?? [])],
  })
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
    ElMessage.success('已从报销草稿移除该文件')
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

async function dismissDurableExpenseItem(item: ExpenseItem): Promise<void> {
  if (props.readonly || durableActionDisabledReason.value || !item.sourceFileId) return
  const scope = beginDurableOperation()
  if (!scope) return
  durableOperating.value = true
  try {
    try {
      await ElMessageBox.confirm(
        '该费用明细来自已识别票据。移除明细后附件仍保留，且刷新后不会自动恢复，是否继续？',
        '移除费用明细',
        {
          confirmButtonText: '移除明细',
          cancelButtonText: '取消',
          type: 'warning',
        },
      )
    } catch {
      return
    }
    if (!acceptsDurableOperation(scope)) return
    expense.removeItem(item.id)
    ElMessage.success('费用明细已移除，原附件仍保留')
    void expense.refreshCalculations()
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
    expense.upsertManualItem({
      id: editor.id || undefined,
      category: editor.category,
      date: editor.date,
      displayDate: editor.date,
      description: editor.description,
      amount: editor.amount,
      receiptCount: editor.receiptCount,
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
    if (item?.sourceFileId) {
      await dismissDurableExpenseItem(item)
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
            添加其他附件
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
        ? '票据/发票上传后会逐个识别并生成费用明细；行程单等材料请使用“添加其他附件”，只保存文件、不做 OCR。文件已保存到当前报销草稿。'
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
      description="请逐张选择“添加到费用明细”或“忽略此票据”；全部确认前不能保存或提交。"
      type="warning"
      :closable="false"
      show-icon
      class="receipt-alert"
    />
    <div
      v-if="props.durable && durableFiles.length"
      class="receipt-list"
      aria-live="polite"
      aria-label="报销草稿附件"
    >
      <article
        v-for="file in durableFiles"
        :key="file.id"
        class="receipt-row"
        :aria-label="`${file.name}：${durableStatusLabel(file)}`"
      >
        <div class="receipt-main">
          <div class="receipt-name-line">
            <strong>{{ file.name }}</strong>
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
            {{ formatFileSize(file.sizeBytes) }} · 已保存到草稿，无本地预览
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
            忽略此票据
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
            <span
              v-if="props.durable && durableFileByItemId(scope.row.id)?.name"
              class="receipt-meta"
            >
              {{ durableFileByItemId(scope.row.id)?.name }} · 已保存，无本地预览
            </span>
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
          {{ durableFileByItemId(item.id)?.name }} · 已保存，无本地预览
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
      <span>预览直接读取本次选择的原文件，不会额外上传或长期保存。</span>
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
        <el-form-item label="金额（元）">
          <el-input
            v-model="editor.amount"
            inputmode="decimal"
            placeholder="0.00"
            maxlength="15"
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
