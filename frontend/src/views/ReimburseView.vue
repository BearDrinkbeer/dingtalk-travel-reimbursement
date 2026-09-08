<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import ExpenseItemsCard from '@/components/reimbursement/ExpenseItemsCard.vue'
import ExpenseSummaryCard from '@/components/reimbursement/ExpenseSummaryCard.vue'
import TravelApprovalSelector from '@/components/reimbursement/TravelApprovalSelector.vue'
import TripSubsidyCard from '@/components/reimbursement/TripSubsidyCard.vue'
import { useAuthStore } from '@/stores/auth'
import { useExpenseStore } from '@/stores/expense'
import { useHealthStore } from '@/stores/health'
import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'
import { useReimbursementSubmissionStore } from '@/stores/reimbursementSubmission'
import { isForeignExpense } from '@/types/expenses'
import { isActiveProof, missingExpenseMaterials, needsMaterialConfirmation, requiresPaymentProof } from '@/utils/expenseProofs'
import type {
  ReimbursementDraft,
  ReimbursementDraftInput,
  ReimbursementRelatedApproval,
  ReimbursementRelatedApprovalSelection,
  ReimbursementSubmissionStatus,
} from '@/types/reimbursements'

const auth = useAuthStore()
const expense = useExpenseStore()
const health = useHealthStore()
const drafts = useReimbursementDraftStore()
const submission = useReimbursementSubmissionStore()
const expenseItemsCard = ref<InstanceType<typeof ExpenseItemsCard> | null>(null)
const departmentId = ref('')
const companyValue = ref('')
const budgetCodeValue = ref('')
const selectedRelatedApprovals = ref<ReimbursementRelatedApprovalSelection[]>([])
const initializingWorkspace = ref(false)
const initializationError = ref('')
const saving = ref(false)
const saveError = ref('')
const submitFlowPending = ref(false)
const replacingTemplate = ref(false)
const refreshingServiceStatus = ref(false)
const savedInputSignature = ref('')
const savedRelatedSignature = ref('')
let calculationTimer: ReturnType<typeof setTimeout> | undefined
let autosaveTimer: ReturnType<typeof setTimeout> | undefined
let initializationPromise: Promise<void> | null = null
let savePromise: Promise<void> | null = null
let initializationScope = ''
let initializedScope = ''
let disposed = false

const companyOptions = computed(() => drafts.reimbursementOptions?.companyOptions ?? [])
const budgetOptions = computed(() => drafts.reimbursementOptions?.budgetCodeOptions ?? [])
const companyLabel = computed(() => companyOptions.value.find((option) => option.value === companyValue.value)?.label ?? companyValue.value)
const budgetLabel = computed(() => budgetOptions.value.find((option) => option.value === budgetCodeValue.value)?.label ?? budgetCodeValue.value)
const selectedTravelTypeLabel = computed(() => {
  const first = selectedRelatedApprovals.value[0]
  if (!first) return ''
  const candidate = drafts.travelApprovals.find(
    (approval) => approval.processInstanceId === first.processInstanceId,
  )
  if (candidate) return candidate.travelTypeOption.label
  const linked = drafts.currentDraft?.relatedApprovals.find(
    (approval) => approval.processInstanceId === first.processInstanceId,
  )
  const profile = drafts.reimbursementOptions?.travelProfiles.find(
    (item) => item.profileKey === first.profileKey,
  )
  if (!profile) return ''
  const mapped = linked?.sourceTravelTypeValue
    ? profile.travelTypeMappings?.[linked.sourceTravelTypeValue]
    : undefined
  return mapped?.label ?? profile.travelTypeOption.label ?? profile.displayName
})
const selectedTravelApprovalPeriods = computed(() => selectedRelatedApprovals.value.flatMap((selection) => {
  const candidate = drafts.travelApprovals.find(
    (approval) => approval.processInstanceId === selection.processInstanceId,
  )
  const linked = drafts.currentDraft?.relatedApprovals.find(
    (approval) => approval.processInstanceId === selection.processInstanceId,
  )
  const approval = candidate ?? linked
  return approval ? [{ startDate: approval.startDate, endDate: approval.endDate }] : []
}))
const selectedTravelPeriod = computed(() => {
  const periods = selectedTravelApprovalPeriods.value
  if (!periods.length) return ''
  const startDate = periods.map((item) => item.startDate).sort()[0]!
  const endDate = periods.map((item) => item.endDate).sort().at(-1)!
  return startDate === endDate ? startDate : `${startDate} 至 ${endDate}`
})
const selectedTravelDateError = computed(() => {
  if (!expense.includeSubsidy || !expense.trip.startDate || !expense.trip.endDate
    || !selectedRelatedApprovals.value.length) return ''
  const periods = selectedTravelApprovalPeriods.value
  if (periods.length !== selectedRelatedApprovals.value.length) return ''
  const approvalStart = periods.map((item) => item.startDate).sort()[0]!
  const approvalEnd = periods.map((item) => item.endDate).sort().at(-1)!
  if (approvalStart > expense.trip.startDate || approvalEnd < expense.trip.endDate) {
    return '所选出差审批日期必须完整覆盖出差补助日期，请调整补助日期或关联审批'
  }
  return ''
})
const trackedSubmission = computed(() => Boolean(drafts.currentDraft
  && submission.activeDraftId === drafts.currentDraft.id
  && (submission.submission || submission.submitting || submission.idempotencyKey)))
const templateMismatch = computed(() => {
  const draft = drafts.currentDraft
  const options = drafts.reimbursementOptions
  return Boolean(draft && options && (
    draft.template.processCode !== options.reimbursementProcessCode
    || draft.template.configVersion !== options.templateConfigVersion
  ))
})
const reimbursementOptionsUnavailable = computed(() => Boolean(drafts.reimbursementOptionsError))
const canReplaceOutdatedForm = computed(() => templateMismatch.value && Boolean(drafts.currentDraft
  && ['DRAFT', 'REVIEW_READY'].includes(drafts.currentDraft.status))
  && !trackedSubmission.value && !submitFlowPending.value && !initializingWorkspace.value)
const formReadOnly = computed(() => !drafts.currentDraft || submitFlowPending.value
  || ['LOCKED', 'EXPIRED'].includes(drafts.currentDraft.status) || trackedSubmission.value
  || templateMismatch.value || reimbursementOptionsUnavailable.value)
const formReadOnlyReason = computed(() => {
  if (!drafts.currentDraft) return '正在准备报销表单'
  if (submitFlowPending.value) return '正在提交，请稍候'
  if (drafts.currentDraft.status === 'EXPIRED') return '本次填写内容已过期，请重新填写'
  if (drafts.currentDraft.status === 'LOCKED' || trackedSubmission.value) return '本次报销内容已锁定，请查看提交进度'
  if (reimbursementOptionsUnavailable.value) return '当前 OA 模板或选项不可用，请重新加载后继续填写'
  if (templateMismatch.value) return 'OA 表单已更新，请按新表单重新填写'
  return ''
})
const currentFormInput = computed<ReimbursementDraftInput>(() => {
  const trip = expense.tripPayload()
  return {
    ocrDispositionVersion: 1,
    companyValue: companyValue.value,
    budgetCodeValue: budgetCodeValue.value,
    trip,
    editingState: {
      includeSubsidy: expense.includeSubsidy,
      trip: {
        ...expense.trip,
        // Persist the same half-day encoding checked by the backend at submission.
        startTime: trip?.startTime ?? expense.trip.startTime,
        endTime: trip?.endTime ?? expense.trip.endTime,
      },
    },
    items: expense.buildDraftExpenseItems(),
    dismissedOcrFileIds: [...expense.dismissedOcrFileIds],
  }
})
const inputDirty = computed(() => inputSignature(currentFormInput.value) !== savedInputSignature.value)
const relatedDirty = computed(() => relatedSignature(selectedRelatedApprovals.value) !== savedRelatedSignature.value)
const formDirty = computed(() => inputDirty.value || relatedDirty.value)
const saveLabel = computed(() => trackedSubmission.value && submission.succeeded ? 'OA 已成功发起'
  : trackedSubmission.value && submission.status === 'QUEUED' ? '已排队，内容已锁定'
  : trackedSubmission.value || drafts.currentDraft?.status === 'LOCKED' ? '内容已锁定'
  : saveError.value ? '保存失败，内容仍保留在本页'
  : saving.value ? '正在保存…' : formDirty.value ? '等待保存…' : '已保存')
const submissionServiceReason = computed(() => submission.oaSubmissionEnabled === false
  ? (trackedSubmission.value || drafts.currentDraft?.status === 'LOCKED'
      ? 'OA提交服务未开启，请保留本次提交并稍后核对' : 'OA提交服务未开启，可继续填写')
  : submission.oaSubmissionEnabled === null ? '正在确认 OA 提交服务状态' : '')
const submissionButtonReason = computed(() => formReadOnlyReason.value || submissionServiceReason.value
  || (drafts.busy ? '请等待材料处理完成' : '')
  || (pendingMaterialFiles.value.length ? `还有 ${pendingMaterialFiles.value.length} 份材料待确认用途` : '')
  || (missingMaterialItems.value.length ? `还有 ${missingMaterialItems.value.length} 笔费用需补材料` : ''))
const missingMaterialItems = computed(() => expense.sortedItems.map((item) => ({ item, missing: missingExpenseMaterials(item, drafts.files) }))
  .filter((entry) => entry.missing.length))
const pendingMaterialFiles = computed(() => drafts.files.filter(needsMaterialConfirmation))
const previewDisabledReason = computed(() => expense.itemReadinessError
  || (!budgetCodeValue.value ? '请先选择预算代码' : '') || (submitFlowPending.value ? '正在提交，请稍候' : ''))
const unresolvedOcrFiles = computed(() => drafts.files.filter((file) =>
  file.status === 'ACTIVE' && file.role === 'EXPENSE_SOURCE'
  && !expense.items.some((item) => item.sourceFileId === file.id)
  && !expense.dismissedOcrFileIds.includes(file.id)))
const submissionProgress: ReimbursementSubmissionStatus[] = [
  'QUEUED', 'VALIDATING', 'GENERATING_EXCEL', 'UPLOADING', 'OA_CREATING', 'VERIFYING', 'SUBMITTED',
]
const submissionProgressPercentage = computed(() => {
  if (submission.terminal) return 100
  const index = submissionProgress.indexOf(submission.status!)
  return index < 0 ? 0 : Math.round(index / (submissionProgress.length - 1) * 100)
})
const submissionAlertType = computed(() => submission.status === 'SUBMITTED' ? 'success'
  : submission.status === 'FAILED_FINAL' ? 'error'
    : submission.status === 'MANUAL_REVIEW' || submission.status === 'FAILED_RETRYABLE' ? 'warning' : 'info')
const canStartNewReimbursement = computed(() => submission.status === 'SUBMITTED'
  || submission.status === 'FAILED_FINAL')

function sessionScope(): string {
  const user = auth.session?.user.userId
  const department = auth.session?.selectedDepartment?.id
  return auth.status === 'authenticated' && user && department ? `${user}:${department}` : ''
}
function inputSignature(input: ReimbursementDraftInput): string {
  // Project is server-derived from budget, never a second employee input.
  return JSON.stringify({
    ocrDispositionVersion: input.ocrDispositionVersion,
    companyValue: input.companyValue,
    budgetCodeValue: input.budgetCodeValue,
    trip: input.trip,
    editingState: input.editingState,
    items: input.items,
    dismissedOcrFileIds: input.dismissedOcrFileIds,
  })
}
function relatedSignature(selections: ReimbursementRelatedApprovalSelection[]): string { return JSON.stringify(selections) }
function acceptDerivedAccounting(): void {
  const input = drafts.currentDraft?.input
  if (!input) return
  companyValue.value = input.companyValue
  budgetCodeValue.value = input.budgetCodeValue
  // Merge only server-owned values; expense edits made during the request stay live.
  if (savedInputSignature.value) {
    const saved = JSON.parse(savedInputSignature.value) as ReimbursementDraftInput
    savedInputSignature.value = inputSignature({ ...saved, companyValue: input.companyValue, budgetCodeValue: input.budgetCodeValue })
  }
}
async function reconfirmRelatedApprovals(): Promise<void> {
  try {
    await flushAutosave()
    const selections = JSON.parse(JSON.stringify(selectedRelatedApprovals.value)) as ReimbursementRelatedApprovalSelection[]
    await drafts.saveRelatedApprovals(selections)
    savedRelatedSignature.value = relatedSignature(selections)
    acceptDerivedAccounting()
  } catch (error) {
    saveError.value = error instanceof Error ? error.message : '出差审批核验失败，请重试'
  }
}
function shanghaiDate(milliseconds: number): string {
  const parts = new Intl.DateTimeFormat('en', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date(milliseconds))
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}
function approvalSelection(approval: ReimbursementRelatedApproval): ReimbursementRelatedApprovalSelection {
  return {
    processInstanceId: approval.processInstanceId, profileKey: approval.profileKey,
    queryWindow: { from: shanghaiDate(approval.queryWindow.startTimeMs), to: shanghaiDate(approval.queryWindow.endTimeMs) },
  }
}
function hydrate(draft: ReimbursementDraft): void {
  companyValue.value = draft.input.companyValue
  budgetCodeValue.value = draft.input.budgetCodeValue
  expense.hydrateFromDraft(draft, drafts.files)
  selectedRelatedApprovals.value = draft.relatedApprovals.map(approvalSelection)
  savedInputSignature.value = inputSignature(draft.input)
  savedRelatedSignature.value = relatedSignature(selectedRelatedApprovals.value)
  saveError.value = ''
}
async function createBlankReimbursement(): Promise<void> {
  const created = await drafts.createDraft({
    ocrDispositionVersion: 1,
    companyValue: '',
    budgetCodeValue: '',
    trip: null,
    editingState: {
      includeSubsidy: false,
      trip: {
        tripType: 'business', startDate: '', startTime: '09:00', endDate: '', endTime: '18:00',
        policyConfirmed: false, confirmedEffectiveDays: '', noSubsidyException: false,
      },
    },
    items: [],
    dismissedOcrFileIds: [],
  })
  // Keep the previous form visible and recoverable if creation fails.
  expense.reset()
  submission.abort()
  hydrate(created)
}
async function runWorkspaceInitialization(scope: string): Promise<void> {
  initializingWorkspace.value = true
  initializationError.value = ''
  try {
    await Promise.all([expense.loadCategories(), drafts.loadReimbursementOptions(), drafts.loadDrafts()])
    if (disposed || sessionScope() !== scope) return
    if (drafts.listError) throw new Error(drafts.listError)
    // Restore a submission first: reloading must not create a duplicate approval.
    const recent = drafts.drafts.find((draft) => draft.status !== 'EXPIRED')
    if (recent) {
      await drafts.loadDraft(recent.id)
      const opened = drafts.currentDraft
      if (!opened || opened.id !== recent.id) throw new Error(drafts.loadError || '报销内容加载失败')
      if (sessionScope() !== scope || disposed) return
      hydrate(opened)
      try {
        await submission.restore(opened.id, opened.status === 'LOCKED'
          ? { discoverByDraft: true, expectedRevision: opened.revision } : undefined)
      } catch { /* The submission card renders the recoverable error. */ }
    } else {
      if (drafts.reimbursementOptionsError) throw new Error(drafts.reimbursementOptionsError)
      await createBlankReimbursement()
    }
    if (drafts.reimbursementOptionsError) initializationError.value = drafts.reimbursementOptionsError
    if (sessionScope() === scope) initializedScope = scope
  } catch (error) {
    if (sessionScope() === scope) initializationError.value = error instanceof Error ? error.message : '报销表单加载失败，请重试'
  } finally {
    if (sessionScope() === scope) { initializingWorkspace.value = false; scheduleAutosave() }
  }
}
function initializeWorkspace(force = false): Promise<void> {
  const scope = sessionScope()
  if (!scope || disposed || (!force && initializedScope === scope)) return Promise.resolve()
  if (initializationPromise && initializationScope === scope) return initializationPromise
  initializationScope = scope
  const running = runWorkspaceInitialization(scope).finally(() => {
    if (initializationPromise === running) initializationPromise = null
  })
  initializationPromise = running
  return running
}
function scheduleAutosave(): void {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  if (disposed || !sessionScope() || initializingWorkspace.value || formReadOnly.value || drafts.busy || !formDirty.value) return
  autosaveTimer = setTimeout(() => { void flushAutosave().catch(() => undefined) }, 600)
}
async function flushAutosave(): Promise<void> {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  if (savePromise) { await savePromise; return flushAutosave() }
  // Never enqueue a snapshot behind a file mutation: its references can already
  // be deleted by the time the queued save runs. The idle watcher retries using
  // the latest form; explicit preview/submit must not treat a skipped save as success.
  if (drafts.busy) throw new Error('请等待材料处理完成后重试')
  if (!drafts.currentDraft || !formDirty.value) return
  const draftId = drafts.currentDraft.id
  const scope = sessionScope()
  const fileOperationPause = new Error('请等待材料处理完成后重试')
  const run = async () => {
    saving.value = true
    saveError.value = ''
    try {
      // Keep typing enabled, save snapshots, and never hydrate an older response over live edits.
      while (formDirty.value && drafts.currentDraft?.id === draftId && sessionScope() === scope) {
        if (drafts.busy) throw fileOperationPause
        if (inputDirty.value) {
          const snapshot = JSON.parse(JSON.stringify(currentFormInput.value)) as ReimbursementDraftInput
          await drafts.saveDraft(snapshot)
          if (drafts.currentDraft?.id !== draftId || sessionScope() !== scope) return
          savedInputSignature.value = inputSignature(snapshot)
          acceptDerivedAccounting()
        }
        if (relatedDirty.value) {
          const selections = JSON.parse(JSON.stringify(selectedRelatedApprovals.value)) as ReimbursementRelatedApprovalSelection[]
          await drafts.saveRelatedApprovals(selections)
          if (drafts.currentDraft?.id !== draftId || sessionScope() !== scope) return
          savedRelatedSignature.value = relatedSignature(selections)
          acceptDerivedAccounting()
        }
      }
    } catch (error) {
      if (error !== fileOperationPause && drafts.currentDraft?.id === draftId && sessionScope() === scope) {
        saveError.value = drafts.mutationError || (error instanceof Error ? error.message : '自动保存失败')
      }
      throw error
    } finally { saving.value = false }
  }
  const running = run()
  savePromise = running
  try { await running } finally { if (savePromise === running) savePromise = null }
}
function validateSubmission(): string {
  if (!companyOptions.value.some((option) => option.value === companyValue.value)) return '请选择所属公司'
  if (!budgetOptions.value.some((option) => option.value === budgetCodeValue.value)) return '请选择预算代码'
  if (expense.includeSubsidy && !expense.tripPayload()) return expense.policyInputError || '请完整填写出发和返回日期、时间'
  if (expense.categoryLoadError || !expense.categories.length) return '请先加载费用类别'
  if (!expense.items.length) return '请至少添加一条费用明细'
  if (expense.itemReadinessError) return expense.itemReadinessError
  if (unresolvedOcrFiles.value.length) return '请处理尚未加入费用明细的票据，或将其仅作为材料保留'
  if (pendingMaterialFiles.value.length) return '请先确认待处理材料的用途'
  if (!selectedRelatedApprovals.value.length) return '请至少关联一张已通过的出差审批'
  if (selectedTravelDateError.value) return selectedTravelDateError.value
  if (!drafts.files.some((file) => file.status === 'ACTIVE')) return '请上传报销材料'
  for (const item of expense.items) {
    if (item.category === 'lodging' && !item.hotelBillFileIds?.some((id) =>
      drafts.files.some((file) => file.id === id && isActiveProof(file, 'hotel_bill')),
    )) return `“${item.description || '住宿费用'}”缺少住宿明细，请补充后提交`
    if ((item.requiresItinerary || item.transportType === 'ride_hailing') && !item.itineraryFileIds?.some((id) =>
      drafts.files.some((file) => file.id === id && isActiveProof(file, 'itinerary')),
    )) return `“${item.description || '网约车费用'}”缺少对应行程单，请点击编辑补齐`
    if (isForeignExpense(item) && !item.cnyAmountConfirmed) {
      return `请确认“${item.description || '国外票据'}”的人民币报销金额`
    }
    if (requiresPaymentProof(item) && !item.paymentProofFileIds?.some((id) =>
      drafts.files.some((file) => file.id === id && isActiveProof(file, 'payment_proof')),
    )) return `“${item.description || '本行费用'}”超过 500 元，请补充付款凭证`
  }
  return ''
}
async function confirmAndSubmit(): Promise<void> {
  if (submissionButtonReason.value) return
  const error = validateSubmission()
  if (error) { ElMessage.warning(error); return }
  submitFlowPending.value = true
  try {
    await ElMessageBox.confirm(
      '核对无误后将生成票据汇总 PDF 和报销单 Excel，并正式提交钉钉 OA。提交后内容将锁定。',
      '提交报销申请', { confirmButtonText: '确认提交', cancelButtonText: '返回检查', type: 'warning' },
    )
    await flushAutosave()
    const ready = await drafts.markReviewReady()
    await submission.submit(ready.id, ready.revision)
    ElMessage.success('正在生成材料并提交 OA')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(submission.errorMessage || saveError.value
      || drafts.mutationError || (error instanceof Error ? error.message : '提交失败，请重试'))
  } finally { submitFlowPending.value = false; scheduleAutosave() }
}
async function newReimbursement(): Promise<void> {
  // Only a definitive outcome permits a new record; uncertain OA creation must
  // retain its existing submission identity until reconciliation completes.
  if (!canStartNewReimbursement.value) return
  initializingWorkspace.value = true
  try { await createBlankReimbursement() }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '新报销准备失败') }
  finally { initializingWorkspace.value = false }
}
async function replaceOutdatedForm(): Promise<void> {
  if (!canReplaceOutdatedForm.value || replacingTemplate.value || drafts.busy) return
  const draftId = drafts.currentDraft?.id
  const scope = sessionScope()
  replacingTemplate.value = true
  try {
    await ElMessageBox.confirm(
      '将打开新版 OA 表单。旧记录和已上传材料会保留，但本页内容不会自动转入新表单，需要重新填写和上传。',
      '按新表单重新填写',
      { confirmButtonText: '重新填写', cancelButtonText: '返回查看', type: 'warning' },
    )
    if (disposed || sessionScope() !== scope || drafts.currentDraft?.id !== draftId
      || !canReplaceOutdatedForm.value || drafts.busy) return
    initializingWorkspace.value = true
    await createBlankReimbursement()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      ElMessage.error(error instanceof Error ? error.message : '新报销准备失败')
    }
  } finally {
    replacingTemplate.value = false
    if (sessionScope() === scope) initializingWorkspace.value = false
  }
}
async function chooseDepartment(): Promise<void> {
  if (!departmentId.value) return
  await auth.selectDepartment(departmentId.value)
  initializedScope = ''
  await initializeWorkspace(true)
}
async function retrySameSubmission(): Promise<void> {
  const draft = drafts.currentDraft
  if (!draft) return
  try { await submission.submit(draft.id, draft.revision) } catch { /* Render the status error. */ }
}
async function retrySubmissionRecovery(): Promise<void> {
  const draft = drafts.currentDraft
  if (!draft) return
  try {
    await submission.restore(draft.id, {
      discoverByDraft: true,
      expectedRevision: draft.revision,
    })
  } catch { /* Render the recovery error in the submission card. */ }
}
async function refreshSubmissionService(refreshProgress = false): Promise<void> {
  if (refreshingServiceStatus.value) return
  refreshingServiceStatus.value = true
  try {
    await auth.refreshPublicConfig()
    if (refreshProgress) await submission.pollNow()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '服务状态刷新失败，请重试')
  } finally { refreshingServiceStatus.value = false }
}
onMounted(async () => { void health.check(); await auth.bootstrap(); await initializeWorkspace() })
watch(() => sessionScope(), () => {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  if (sessionScope()) void initializeWorkspace()
  else { initializedScope = ''; initializationScope = '' }
})
watch([currentFormInput, selectedRelatedApprovals], scheduleAutosave, { deep: true })
watch(() => drafts.busy, () => {
  // A file operation becoming idle should resume saving. Our own failed save
  // must keep the existing explicit-retry behavior, not retry every 600 ms.
  if (!saving.value) scheduleAutosave()
})
watch(budgetLabel, (label) => { expense.manualProjectText = label })
watch(() => [sessionScope(), expense.includeSubsidy, expense.trip, expense.items, drafts.processingFiles, formReadOnly.value], () => {
  if (calculationTimer) clearTimeout(calculationTimer)
  const scope = sessionScope()
  if (!scope || disposed || drafts.processingFiles || formReadOnly.value) return
  calculationTimer = setTimeout(() => {
    if (!disposed && sessionScope() === scope && !drafts.processingFiles && !formReadOnly.value) void expense.refreshCalculations()
  }, 250)
}, { deep: true })
function warnBeforeUnload(event: BeforeUnloadEvent): void {
  if (!formReadOnly.value && formDirty.value) { event.preventDefault(); event.returnValue = '' }
}
onMounted(() => window.addEventListener('beforeunload', warnBeforeUnload))
onBeforeUnmount(() => {
  disposed = true
  if (calculationTimer) clearTimeout(calculationTimer)
  if (autosaveTimer) clearTimeout(autosaveTimer)
  window.removeEventListener('beforeunload', warnBeforeUnload)
  submission.abort()
})
</script>

<template>
  <main class="page-shell">
    <section
      class="hero"
      aria-labelledby="page-title"
    >
      <div>
        <p class="eyebrow">
          <span>钉钉工作台应用</span>
          <span
            v-if="auth.session?.user.userId"
            class="current-user-id"
            :title="`当前 userId：${auth.session.user.userId}`"
          >
            userId：{{ auth.session.user.userId }}
          </span>
        </p>
        <h1 id="page-title">
          {{ auth.appTitle }}
        </h1>
        <p class="summary">
          选预算、上传材料、核对费用，自动生成票据汇总和报销单并提交 OA。
        </p>
      </div>
      <div class="hero-actions">
        <RouterLink
          v-if="auth.isAdmin"
          to="/admin/settings"
        >
          系统设置
        </RouterLink>
        <el-tag
          :type="health.available === true ? 'success' : health.available === false ? 'danger' : 'info'"
          round
        >
          {{ health.label }}
        </el-tag>
      </div>
    </section>
    <el-card
      v-if="auth.status === 'loading' || auth.status === 'idle'"
      shadow="never"
      class="content-card"
    >
      <el-skeleton
        :rows="4"
        animated
      />
    </el-card>
    <el-card
      v-else-if="auth.status === 'mock_required'"
      shadow="never"
      class="content-card"
    >
      <el-result
        icon="info"
        title="开发免登已启用"
        sub-title="此入口只在开发或测试环境出现。"
      >
        <template #extra>
          <el-button
            type="primary"
            @click="auth.useDevelopmentMock"
          >
            使用固定测试身份
          </el-button>
        </template>
      </el-result>
    </el-card>
    <el-card
      v-else-if="auth.status === 'department_required'"
      shadow="never"
      class="content-card"
    >
      <template #header>
        <strong>选择本次报销部门</strong>
      </template>
      <el-select
        v-model="departmentId"
        placeholder="请选择部门"
        aria-label="本次报销部门"
        class="full-width"
      >
        <el-option
          v-for="department in auth.session?.departments"
          :key="department.id"
          :label="department.name"
          :value="department.id"
        />
      </el-select>
      <el-button
        class="block-action"
        type="primary"
        :disabled="!departmentId"
        @click="chooseDepartment"
      >
        确认部门
      </el-button>
    </el-card>
    <template v-else-if="auth.status === 'authenticated'">
      <el-card
        v-if="initializingWorkspace"
        shadow="never"
        class="content-card"
        aria-busy="true"
      >
        <el-skeleton
          :rows="6"
          animated
        />
      </el-card>
      <template v-else>
        <el-alert
          v-if="initializationError"
          :title="initializationError"
          type="error"
          :closable="false"
          class="workspace-alert"
        >
          <template #default>
            <el-button
              link
              type="primary"
              @click="initializeWorkspace(true)"
            >
              重新加载
            </el-button>
          </template>
        </el-alert>
        <template v-if="drafts.currentDraft">
          <el-card
            shadow="never"
            class="content-card reimbursement-card"
            data-testid="reimbursement-basics-card"
          >
            <template #header>
              <div class="card-header">
                <strong>基本信息</strong>
              </div>
            </template>
            <el-descriptions
              :column="2"
              border
              class="identity-grid"
            >
              <el-descriptions-item label="姓名">
                {{ auth.session?.user.name }}
              </el-descriptions-item>
              <el-descriptions-item label="部门">
                {{ auth.session?.selectedDepartment?.name }}
              </el-descriptions-item>
            </el-descriptions>

            <TravelApprovalSelector
              v-model="selectedRelatedApprovals"
              :linked-approvals="drafts.currentDraft.relatedApprovals"
              :readonly="formReadOnly || drafts.processingFiles"
              :required-start-date="expense.includeSubsidy ? expense.trip.startDate : ''"
              :required-end-date="expense.includeSubsidy ? expense.trip.endDate : ''"
            />
            <el-alert
              v-if="drafts.currentDraft.relatedApprovals.length && !drafts.currentDraft.input.accountingSourceVerified && !formReadOnly"
              title="此报销需重新核验关联审批的所属公司和预算代码"
              type="warning"
              :closable="false"
              class="accounting-verification-alert"
            >
              <el-button
                :disabled="drafts.busy || saving"
                @click="reconfirmRelatedApprovals"
              >
                重新确认出差审批
              </el-button>
            </el-alert>

            <section
              class="derived-accounting-section"
              data-testid="derived-accounting-summary"
              aria-labelledby="derived-accounting-heading"
            >
              <div class="derived-accounting-heading">
                <div>
                  <h2 id="derived-accounting-heading">
                    已自动带入
                  </h2>
                  <p>以所选出差审批为准，无需重复填写。</p>
                </div>
              </div>
              <el-descriptions
                :column="2"
                border
                class="derived-accounting-grid"
              >
                <el-descriptions-item label="所属公司">
                  {{ companyLabel || '选择出差审批后自动填入' }}
                </el-descriptions-item>
                <el-descriptions-item label="预算代码 / 项目">
                  {{ budgetLabel || '选择出差审批后自动填入' }}
                </el-descriptions-item>
                <el-descriptions-item label="出差类别">
                  {{ selectedTravelTypeLabel || '选择出差审批后自动填入' }}
                </el-descriptions-item>
                <el-descriptions-item label="出差日期">
                  {{ selectedTravelPeriod || '选择出差审批后自动填入' }}
                </el-descriptions-item>
              </el-descriptions>
              <p class="field-help">
                所属公司和预算代码由关联审批自动填入，不可修改；预算代码完整名称会填入报销单 Excel 的项目栏。
              </p>
            </section>
            <el-alert
              v-if="formReadOnlyReason"
              :title="formReadOnlyReason"
              type="info"
              :closable="false"
            />
            <el-button
              v-if="canReplaceOutdatedForm"
              class="block-action"
              type="primary"
              :loading="replacingTemplate"
              :disabled="drafts.busy"
              @click="replaceOutdatedForm"
            >
              按新表单重新填写
            </el-button>
          </el-card>
          <fieldset
            class="editor-fieldset"
            :disabled="formReadOnly || drafts.processingFiles"
          >
            <TripSubsidyCard
              :readonly="formReadOnly"
            />
            <ExpenseItemsCard
              ref="expenseItemsCard"
              :durable="true"
              :readonly="formReadOnly"
            />
          </fieldset>
          <ExpenseSummaryCard
            :preview-disabled-reason="previewDisabledReason"
            :before-preview="flushAutosave"
          />
          <el-card
            shadow="never"
            class="content-card submission-card"
          >
            <div class="submission-actions">
              <div
                role="status"
                aria-live="polite"
                data-testid="autosave-status"
              >
                <span>{{ saveLabel }}</span><el-button
                  v-if="saveError"
                  link
                  type="primary"
                  @click="flushAutosave().catch(() => undefined)"
                >
                  重试保存
                </el-button>
              </div>
              <div class="primary-submit-area">
                <p>OA 附件为两个文件：票据汇总.pdf（含行程单、付款凭证）＋报销单.xlsx。</p>
                <el-button
                  type="primary"
                  size="large"
                  :loading="submitFlowPending || submission.submitting"
                  :disabled="Boolean(submissionButtonReason)"
                  :title="submissionButtonReason"
                  @click="confirmAndSubmit"
                >
                  提交 OA
                </el-button>
              </div>
            </div>
            <div
              v-if="!formReadOnly && (missingMaterialItems.length || pendingMaterialFiles.length)"
              class="material-checklist"
              data-testid="material-checklist"
              role="status"
            >
              <strong v-if="missingMaterialItems.length">还有 {{ missingMaterialItems.length }} 笔费用需补材料</strong>
              <div
                v-for="entry in missingMaterialItems"
                :key="entry.item.id"
                class="material-checklist-row"
              >
                <span>{{ entry.item.description || '费用明细' }} · ¥{{ entry.item.amount }} · 缺{{ entry.missing.join('、') }}</span>
                <el-button
                  link
                  type="primary"
                  @click="expenseItemsCard?.focusMaterial(entry.item.id)"
                >
                  去补齐
                </el-button>
              </div>
              <div
                v-if="pendingMaterialFiles.length"
                class="material-checklist-row"
              >
                <span>{{ pendingMaterialFiles.length }} 份材料待确认用途</span>
                <el-button
                  link
                  type="primary"
                  @click="expenseItemsCard?.focusMaterial()"
                >
                  去确认
                </el-button>
              </div>
              <p>补齐后可提交 OA；你仍可继续编辑和预览报销单。</p>
            </div>
            <el-alert
              v-if="submissionServiceReason"
              :title="submissionServiceReason"
              type="warning"
              :closable="false"
              class="submission-status"
              show-icon
            >
              <el-button
                link
                type="primary"
                :loading="refreshingServiceStatus"
                @click="refreshSubmissionService()"
              >
                刷新服务状态
              </el-button>
            </el-alert>
            <el-alert
              v-if="saveError"
              :title="saveError"
              type="error"
              :closable="false"
              class="submission-status"
            />
            <section
              v-if="submission.activeDraftId === drafts.currentDraft.id && (submission.progressLabel || submission.errorMessage)"
              class="submission-status"
              aria-live="polite"
              data-testid="submission-status"
            >
              <el-alert
                :title="submission.progressLabel || '提交需要处理'"
                :description="submission.errorMessage || undefined"
                :type="submissionAlertType"
                :closable="false"
                show-icon
              />
              <el-progress
                :percentage="submissionProgressPercentage"
                :status="submission.status === 'SUBMITTED' ? 'success' : submission.status === 'FAILED_FINAL' ? 'exception' : undefined"
              />
              <p v-if="submission.submission?.businessId">
                审批编号：{{ submission.submission.businessId }}
              </p>
              <p v-if="submission.status === 'MANUAL_REVIEW'">
                暂时无法确认提交结果，请联系管理员并提供提交编号 {{ submission.submission?.submissionId }}，不要重复发起报销。
              </p>
              <div class="submission-status-actions">
                <el-button
                  v-if="submission.status === 'MANUAL_REVIEW' && submission.submission?.processInstanceId"
                  type="primary"
                  :loading="submission.submitting"
                  @click="submission.recheck()"
                >
                  重新核对（不会重复提交）
                </el-button>
                <a
                  v-if="submission.status === 'SUBMITTED' && submission.submission?.approvalUrl?.trim()"
                  :href="submission.submission.approvalUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                  data-testid="approval-link"
                ><el-button type="primary">打开钉钉 OA</el-button></a>
                <el-button
                  v-if="canStartNewReimbursement"
                  @click="newReimbursement"
                >
                  {{ submission.status === 'FAILED_FINAL' ? '重新填写' : '再报销一笔' }}
                </el-button>
                <el-button
                  v-if="submission.submission && !submission.terminal"
                  :loading="refreshingServiceStatus"
                  @click="refreshSubmissionService(true)"
                >
                  刷新提交进度
                </el-button>
                <el-button
                  v-if="submission.requestError && !submission.submission"
                  :loading="submission.submitting"
                  :disabled="submission.idempotencyKey ? submission.oaSubmissionEnabled !== true : false"
                  @click="submission.idempotencyKey ? retrySameSubmission() : retrySubmissionRecovery()"
                >
                  {{ submission.idempotencyKey ? '重试本次提交' : '重新查找提交记录' }}
                </el-button>
              </div>
            </section>
          </el-card>
        </template>
      </template>
    </template>
    <el-card
      v-else
      shadow="never"
      class="content-card"
    >
      <el-result
        icon="error"
        title="无法进入报销工具"
        :sub-title="auth.errorMessage || '请从公司钉钉工作台重新进入本应用'"
      >
        <template #extra>
          <el-button
            type="primary"
            @click="auth.bootstrap(true)"
          >
            重新尝试
          </el-button>
        </template>
      </el-result>
    </el-card>
  </main>
</template>

<style scoped>
.hero-actions { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.eyebrow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.current-user-id {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0;
  overflow-wrap: anywhere;
}
.workspace-alert { margin-bottom: 18px; }
.identity-grid { margin-bottom: 0; }
.plain-fieldset, .editor-fieldset { min-width: 0; padding: 0; margin: 0; border: 0; }
.accounting-verification-alert { margin-top: 18px; }
.derived-accounting-section { margin-top: 22px; padding-top: 22px; border-top: 1px solid var(--el-border-color-lighter); }
.derived-accounting-heading h2 { margin: 0; color: var(--el-text-color-primary); font-size: 16px; }
.derived-accounting-heading p { margin: 6px 0 14px; color: var(--el-text-color-secondary); font-size: 13px; }
.derived-accounting-grid { margin-bottom: 12px; }
.derived-accounting-grid :deep(.el-descriptions__table) { table-layout: fixed; }
.derived-accounting-grid :deep(.el-descriptions__content) { overflow-wrap: anywhere; }
.submission-actions, .submission-status-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.primary-submit-area { text-align: right; }
.primary-submit-area p { color: var(--el-text-color-secondary); font-size: 13px; }
.submission-status { margin-top: 20px; }
.submission-status .el-progress { margin: 16px 0; }
.material-checklist { margin-top: 16px; padding: 14px 16px; border-radius: 10px; background: #fff8ed; color: #8b5a16; font-size: 13px; }
.material-checklist-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 8px; }
.material-checklist-row > span { min-width: 0; overflow-wrap: anywhere; }
.material-checklist-row .el-button { flex-shrink: 0; }
.material-checklist p { margin: 8px 0 0; color: #667085; }
@media (max-width: 640px) {
  .primary-submit-area { text-align: left; }
}
</style>
