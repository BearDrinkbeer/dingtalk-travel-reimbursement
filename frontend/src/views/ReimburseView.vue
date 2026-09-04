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
import { useProjectsStore } from '@/stores/projects'
import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'
import { useReimbursementSubmissionStore } from '@/stores/reimbursementSubmission'
import type { ExcelProjectInput } from '@/types/expenses'
import type {
  ReimbursementDraft,
  ReimbursementDraftFile,
  ReimbursementDraftInput,
  ReimbursementDraftStatus,
  ReimbursementRelatedApproval,
  ReimbursementRelatedApprovalSelection,
  ReimbursementSubmissionStatus,
} from '@/types/reimbursements'

type NewProjectMode = 'selected' | 'manual'

const auth = useAuthStore()
const expense = useExpenseStore()
const health = useHealthStore()
const projects = useProjectsStore()
const drafts = useReimbursementDraftStore()
const submission = useReimbursementSubmissionStore()

const departmentId = ref('')
const companyValue = ref('')
const budgetCodeValue = ref('')
const selectedRelatedApprovals = ref<ReimbursementRelatedApprovalSelection[]>([])
const createDialogVisible = ref(false)
const newCompanyValue = ref('')
const newBudgetCodeValue = ref('')
const newProjectMode = ref<NewProjectMode>('selected')
const newProjectId = ref<number | null>(null)
const newManualProject = ref('')
const initializingWorkspace = ref(false)
const initializationError = ref('')
const savingDraft = ref(false)
const switchingDraft = ref(false)
const creatingDraft = ref(false)
const submitFlowPending = ref(false)
let calculationTimer: ReturnType<typeof setTimeout> | undefined
let initializationPromise: Promise<void> | null = null
let initializationScope = ''
let initializedScope = ''

const draftStatusLabels: Record<ReimbursementDraftStatus, string> = {
  DRAFT: '草稿',
  REVIEW_READY: '已检查',
  LOCKED: '提交中/已提交',
  EXPIRED: '已过期',
}
const submissionProgress: ReimbursementSubmissionStatus[] = [
  'QUEUED',
  'VALIDATING',
  'GENERATING_EXCEL',
  'UPLOADING',
  'OA_CREATING',
  'VERIFYING',
  'SUBMITTED',
]

const statusType = computed(() =>
  health.available === true ? 'success' : health.available === false ? 'danger' : 'info',
)
const companyOptions = computed(() => drafts.reimbursementOptions?.companyOptions ?? [])
const budgetOptions = computed(() => drafts.reimbursementOptions?.budgetCodeOptions ?? [])
const currentDraftId = computed(() => drafts.currentDraft?.id ?? '')
const trackedSubmissionForCurrentDraft = computed(() => Boolean(
  drafts.currentDraft
  && submission.activeDraftId === drafts.currentDraft.id
  && (
    submission.submission !== null
    || submission.submitting
    || submission.idempotencyKey !== null
  ),
))
const draftInteractionPending = computed(() => savingDraft.value || submitFlowPending.value)
const formReadOnly = computed(() => {
  const draft = drafts.currentDraft
  return draft === null
    || draftInteractionPending.value
    || draft.status === 'LOCKED'
    || draft.status === 'EXPIRED'
    || trackedSubmissionForCurrentDraft.value
})
const formReadOnlyReason = computed(() => {
  const draft = drafts.currentDraft
  if (!draft) return '请先创建或打开一份报销草稿'
  if (draftInteractionPending.value) return '当前操作进行中，请等待完成后再修改'
  if (draft.status === 'EXPIRED') return '该草稿已过期，不能继续修改'
  if (trackedSubmissionForCurrentDraft.value || draft.status === 'LOCKED') {
    return '该报销已进入正式提交阶段，内容已锁定'
  }
  return ''
})
const currentFormInput = computed<ReimbursementDraftInput | null>(() => buildDraftInput())
const unresolvedOcrFiles = computed(() => drafts.files.filter((file) =>
  file.status === 'ACTIVE'
  && file.role === 'EXPENSE_SOURCE'
  && ['COMPLETE', 'FAILED'].includes(file.ocrStatus)
  && !expense.items.some((item) => item.sourceFileId === file.id)
  && !expense.dismissedOcrFileIds.includes(file.id),
))
const inputDirty = computed(() => {
  const draft = drafts.currentDraft
  if (!draft) return false
  return currentFormInput.value === null
    || inputSignature(currentFormInput.value) !== inputSignature(draft.input)
})
const relatedDirty = computed(() => {
  const draft = drafts.currentDraft
  if (!draft) return false
  return relatedSignature(selectedRelatedApprovals.value)
    !== relatedSignature(draft.relatedApprovals.map(selectionFromRelatedApproval))
})
const formDirty = computed(() => inputDirty.value || relatedDirty.value)
const previewDisabledReason = computed(() => {
  if (draftInteractionPending.value) return '当前操作进行中，请等待完成后再预览'
  if (formDirty.value) return '表单有未保存修改，请先保存草稿再预览'
  if (drafts.currentDraft?.status === 'EXPIRED') return '草稿已过期，无法生成预览'
  return ''
})
const submissionProgressPercentage = computed(() => {
  const status = submission.status
  if (status === null) return 0
  if (status === 'FAILED_RETRYABLE' || status === 'RECONCILING') return 78
  if (status === 'ORPHAN_CLEANUP' || status === 'FAILED_FINAL') return 100
  if (status === 'MANUAL_REVIEW') return 100
  const index = submissionProgress.indexOf(status)
  return index < 0 ? 0 : Math.round((index / (submissionProgress.length - 1)) * 100)
})
const submissionAlertType = computed<'success' | 'warning' | 'error' | 'info'>(() => {
  if (submission.status === 'SUBMITTED') return 'success'
  if (submission.status === 'FAILED_FINAL') return 'error'
  if (
    submission.status === 'FAILED_RETRYABLE'
    || submission.status === 'MANUAL_REVIEW'
    || submission.status === 'RECONCILING'
    || submission.status === 'ORPHAN_CLEANUP'
  ) return 'warning'
  return 'info'
})
const submissionButtonReason = computed(() => {
  if (!drafts.currentDraft) return '请先创建报销草稿'
  if (formReadOnlyReason.value) return formReadOnlyReason.value
  if (drafts.busy || savingDraft.value || submitFlowPending.value) return '请等待当前操作完成'
  return ''
})

function sessionScope(): string {
  if (auth.status !== 'authenticated') return ''
  const userId = auth.session?.user.userId
  const department = auth.session?.selectedDepartment?.id
  return userId && department ? `${userId}:${department}` : ''
}

function buildDraftInput(): ReimbursementDraftInput | null {
  const project = expense.projectPayload()
  const trip = expense.tripPayload()
  if (!project || (expense.includeSubsidy && trip === null)) return null
  return {
    ocrDispositionVersion: 1,
    companyValue: companyValue.value.trim(),
    budgetCodeValue: budgetCodeValue.value.trim(),
    project,
    trip,
    items: expense.buildDraftExpenseItems(),
    dismissedOcrFileIds: [...expense.dismissedOcrFileIds],
  }
}

function inputSignature(input: ReimbursementDraftInput): string {
  return JSON.stringify({
    ocrDispositionVersion: input.ocrDispositionVersion,
    companyValue: input.companyValue.trim(),
    budgetCodeValue: input.budgetCodeValue.trim(),
    project: input.project,
    trip: input.trip,
    items: input.items,
    dismissedOcrFileIds: input.dismissedOcrFileIds,
  })
}

function relatedSignature(selections: ReimbursementRelatedApprovalSelection[]): string {
  return JSON.stringify(selections.map((selection) => ({
    processInstanceId: selection.processInstanceId,
    profileKey: selection.profileKey,
    queryWindow: selection.queryWindow,
  })))
}

function dateInShanghai(milliseconds: number): string {
  const parts = new Intl.DateTimeFormat('en', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(milliseconds))
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

function selectionFromRelatedApproval(
  approval: ReimbursementRelatedApproval,
): ReimbursementRelatedApprovalSelection {
  return {
    processInstanceId: approval.processInstanceId,
    profileKey: approval.profileKey,
    queryWindow: {
      from: dateInShanghai(approval.queryWindow.startTimeMs),
      to: dateInShanghai(approval.queryWindow.endTimeMs),
    },
  }
}

function hydrateDraftForm(
  draft: ReimbursementDraft,
  files: readonly ReimbursementDraftFile[],
  replaceRelated = true,
): void {
  companyValue.value = draft.input.companyValue
  budgetCodeValue.value = draft.input.budgetCodeValue
  expense.hydrateFromDraft(draft, files)
  if (replaceRelated) {
    selectedRelatedApprovals.value = draft.relatedApprovals.map(selectionFromRelatedApproval)
  }
}

function clearLocalForm(): void {
  companyValue.value = ''
  budgetCodeValue.value = ''
  selectedRelatedApprovals.value = []
  expense.reset()
}

async function openDraft(draftId: string, confirmDirty = true): Promise<boolean> {
  if (!draftId) return false
  if (confirmDirty && formDirty.value) {
    try {
      await ElMessageBox.confirm(
        '当前草稿有尚未保存的修改。切换后这些修改会丢失，是否继续？',
        '切换报销草稿',
        {
          confirmButtonText: '放弃修改并切换',
          cancelButtonText: '继续编辑',
          type: 'warning',
        },
      )
    } catch {
      return false
    }
  }
  switchingDraft.value = true
  submission.abort()
  try {
    await drafts.loadDraft(draftId)
    const opened = drafts.currentDraft
    if (!opened || opened.id !== draftId) {
      if (drafts.loadError) ElMessage.error(drafts.loadError)
      return false
    }
    hydrateDraftForm(opened, drafts.files)
    try {
      await submission.restore(
        opened.id,
        opened.status === 'LOCKED'
          ? { discoverByDraft: true, expectedRevision: opened.revision }
          : undefined,
      )
    } catch {
      // The draft remains visible; the status panel exposes the recoverable error.
    }
    return true
  } finally {
    switchingDraft.value = false
  }
}

async function runWorkspaceInitialization(scope: string): Promise<void> {
  initializingWorkspace.value = true
  initializationError.value = ''
  try {
    await Promise.all([
      projects.search(),
      expense.loadCategories(),
      drafts.loadReimbursementOptions(),
      drafts.loadDrafts(),
    ])
    if (sessionScope() !== scope) return
    if (drafts.reimbursementOptionsError || drafts.listError) {
      initializationError.value = [
        drafts.reimbursementOptionsError,
        drafts.listError,
      ].filter(Boolean).join('；')
    }
    const recent = drafts.drafts.find((draft) => draft.status !== 'EXPIRED')
    if (recent) await openDraft(recent.id, false)
    else clearLocalForm()
    if (sessionScope() === scope) initializedScope = scope
  } catch (error) {
    if (sessionScope() === scope) {
      initializationError.value = error instanceof Error
        ? error.message
        : '报销工作区加载失败，请重试'
    }
  } finally {
    if (sessionScope() === scope) initializingWorkspace.value = false
  }
}

function initializeWorkspace(force = false): Promise<void> {
  const scope = sessionScope()
  if (!scope) return Promise.resolve()
  if (!force && initializedScope === scope) return Promise.resolve()
  if (!force && initializationPromise && initializationScope === scope) {
    return initializationPromise
  }
  initializationScope = scope
  const running = runWorkspaceInitialization(scope).finally(() => {
    if (initializationPromise === running) initializationPromise = null
  })
  initializationPromise = running
  return running
}

onMounted(async () => {
  void health.check()
  await auth.bootstrap()
  if (auth.status === 'authenticated') await initializeWorkspace()
})

watch(
  () => [
    auth.status,
    auth.session?.user.userId ?? '',
    auth.session?.selectedDepartment?.id ?? '',
  ].join(':'),
  () => {
    if (auth.status === 'authenticated') void initializeWorkspace()
    else {
      initializedScope = ''
      initializationScope = ''
    }
  },
)
watch(
  () => [
    expense.includeSubsidy,
    expense.trip.tripType,
    expense.trip.startDate,
    expense.trip.startTime,
    expense.trip.endDate,
    expense.trip.endTime,
    expense.trip.policyConfirmed,
    expense.trip.confirmedEffectiveDays,
    expense.trip.noSubsidyException,
    expense.items
      .map((item) => [
        item.id,
        item.category,
        item.date,
        item.displayDate,
        item.description,
        item.amount,
        item.receiptCount,
      ].join(':'))
      .join('|'),
  ],
  () => {
    if (calculationTimer) clearTimeout(calculationTimer)
    calculationTimer = setTimeout(() => void expense.refreshCalculations(), 250)
  },
)
onBeforeUnmount(() => {
  if (calculationTimer) clearTimeout(calculationTimer)
  submission.abort()
})

async function chooseDepartment(): Promise<void> {
  if (!departmentId.value) return
  await auth.selectDepartment(departmentId.value)
  initializedScope = ''
  await initializeWorkspace(true)
}

async function retryInitialization(): Promise<void> {
  initializedScope = ''
  await initializeWorkspace(true)
}

async function chooseDraft(value: string): Promise<void> {
  if (
    draftInteractionPending.value
    || switchingDraft.value
    || !value
    || value === drafts.currentDraft?.id
  ) return
  await openDraft(value)
}

async function requestNewDraft(): Promise<void> {
  if (draftInteractionPending.value) return
  if (formDirty.value) {
    try {
      await ElMessageBox.confirm(
        '当前草稿有尚未保存的修改。新建后这些修改会丢失，是否继续？',
        '新建报销草稿',
        {
          confirmButtonText: '放弃修改并新建',
          cancelButtonText: '继续编辑',
          type: 'warning',
        },
      )
    } catch {
      return
    }
  }
  newCompanyValue.value = ''
  newBudgetCodeValue.value = ''
  newProjectMode.value = 'selected'
  newProjectId.value = null
  newManualProject.value = ''
  createDialogVisible.value = true
}

function newProjectPayload(): ExcelProjectInput | null {
  if (newProjectMode.value === 'manual') {
    const text = newManualProject.value.trim()
    return text ? { mode: 'manual', text } : null
  }
  return newProjectId.value && newProjectId.value > 0
    ? { mode: 'selected', id: newProjectId.value }
    : null
}

async function createNewDraft(): Promise<void> {
  const project = newProjectPayload()
  if (!newCompanyValue.value) {
    ElMessage.warning('请选择 OA 所属公司')
    return
  }
  if (!newBudgetCodeValue.value) {
    ElMessage.warning('请选择 OA 预算代码')
    return
  }
  if (!project) {
    ElMessage.warning(
      newProjectMode.value === 'manual' ? '请填写 Excel 内部项目' : '请选择 Excel 内部项目',
    )
    return
  }
  creatingDraft.value = true
  try {
    const created = await drafts.createDraft({
      ocrDispositionVersion: 1,
      companyValue: newCompanyValue.value,
      budgetCodeValue: newBudgetCodeValue.value,
      project,
      trip: null,
      items: [],
      dismissedOcrFileIds: [],
    })
    if (drafts.currentDraft?.id === created.id) {
      submission.abort()
      hydrateDraftForm(created, [])
      createDialogVisible.value = false
      ElMessage.success('草稿已创建，现在可以上传票据并填写明细')
    }
  } catch (error) {
    ElMessage.error(
      drafts.mutationError
      || (error instanceof Error && error.message ? error.message : '草稿创建失败，请重试'),
    )
  } finally {
    creatingDraft.value = false
  }
}

function validateInput(requireComplete: boolean): ReimbursementDraftInput | null {
  if (!drafts.currentDraft) {
    ElMessage.warning('请先创建或打开报销草稿')
    return null
  }
  if (formReadOnly.value) {
    ElMessage.warning(formReadOnlyReason.value)
    return null
  }
  if (!companyOptions.value.some((option) => option.value === companyValue.value)) {
    ElMessage.warning('请选择当前 OA 模板提供的所属公司')
    return null
  }
  if (!budgetOptions.value.some((option) => option.value === budgetCodeValue.value)) {
    ElMessage.warning('请选择当前 OA 模板提供的预算代码')
    return null
  }
  if (!expense.projectPayload()) {
    ElMessage.warning(expense.manualProject ? '请填写 Excel 内部项目' : '请选择 Excel 内部项目')
    return null
  }
  if (expense.includeSubsidy && expense.tripPayload() === null) {
    ElMessage.warning(expense.policyInputError || '请完整填写出发和返回日期、时间')
    return null
  }
  const input = buildDraftInput()
  if (!input) return null
  if (expense.categoryLoadError || expense.categories.length === 0) {
    ElMessage.warning('费用类别尚未正确加载')
    return null
  }
  if (expense.items.length > expense.maxExpenseItems) {
    ElMessage.warning(`报销单最多填写 ${expense.maxExpenseItems} 条费用明细`)
    return null
  }
  if (expense.itemReadinessError) {
    ElMessage.warning(expense.itemReadinessError)
    return null
  }
  if (unresolvedOcrFiles.value.length > 0) {
    ElMessage.warning(
      `仍有 ${unresolvedOcrFiles.value.length} 张票据的 OCR 结果尚未决定，请选择“添加到费用明细”或“忽略此票据”`,
    )
    return null
  }
  if (expense.items.some(
    (item) => !expense.manualCategories.some((category) => category.id === item.category),
  )) {
    ElMessage.warning('费用明细中存在不可用类别')
    return null
  }
  if (!requireComplete) return input
  if (input.items.length === 0) {
    ElMessage.warning('请至少添加一条完整的费用明细')
    return null
  }
  if (selectedRelatedApprovals.value.length === 0) {
    ElMessage.warning('请至少关联一张已通过的出差审批')
    return null
  }
  if (!drafts.files.some((file) => file.status === 'ACTIVE')) {
    ElMessage.warning('请至少上传一个有效附件')
    return null
  }
  if (drafts.files.some(
    (file) => ['RESERVED', 'WRITING', 'DELETING'].includes(file.status)
      || file.ocrStatus === 'RUNNING',
  )) {
    ElMessage.warning('附件仍在上传、删除或识别中，请稍后再提交')
    return null
  }
  return input
}

async function persistDraftInput(
  input: ReimbursementDraftInput,
  persistRelated: boolean,
): Promise<ReimbursementDraft> {
  const inputSnapshot = inputSignature(input)
  const relatedSnapshot = selectedRelatedApprovals.value.map((selection) => ({
    processInstanceId: selection.processInstanceId,
    profileKey: selection.profileKey,
    queryWindow: { ...selection.queryWindow },
  }))
  const relatedSnapshotSignature = relatedSignature(relatedSnapshot)
  const saveRelated = persistRelated && relatedDirty.value
  let saved = await drafts.saveDraft(input)
  if (saveRelated) saved = await drafts.saveRelatedApprovals(relatedSnapshot)

  const liveInput = currentFormInput.value
  if (liveInput && inputSignature(liveInput) === inputSnapshot) {
    const relatedUnchanged = relatedSignature(selectedRelatedApprovals.value)
      === relatedSnapshotSignature
    hydrateDraftForm(saved, drafts.files, persistRelated && relatedUnchanged)
  }
  return saved
}

async function saveCurrentDraft(): Promise<void> {
  const input = validateInput(false)
  if (!input || savingDraft.value) return
  savingDraft.value = true
  try {
    await persistDraftInput(input, true)
    ElMessage.success('草稿已保存')
  } catch (error) {
    ElMessage.error(
      drafts.mutationError
      || (error instanceof Error && error.message ? error.message : '草稿保存失败，请重试'),
    )
  } finally {
    savingDraft.value = false
  }
}

async function confirmAndSubmit(): Promise<void> {
  if (submitFlowPending.value || submissionButtonReason.value) return
  const input = validateInput(true)
  if (!input) return
  submitFlowPending.value = true
  try {
    await ElMessageBox.confirm(
      '提交后系统会正式发起钉钉 OA 审批，报销内容和附件将被锁定，不能再修改。是否确认提交？',
      '确认并提交到钉钉 OA',
      {
        confirmButtonText: '确认正式提交',
        cancelButtonText: '返回检查',
        type: 'warning',
      },
    )
    await persistDraftInput(input, false)
    if (relatedDirty.value) {
      const withRelated = await drafts.saveRelatedApprovals(selectedRelatedApprovals.value)
      hydrateDraftForm(withRelated, drafts.files)
    }
    const ready = await drafts.markReviewReady()
    hydrateDraftForm(ready, drafts.files)
    await submission.submit(ready.id, ready.revision)
    ElMessage.success('正式提交任务已创建，系统正在处理')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(
      submission.errorMessage
      || drafts.mutationError
      || (error instanceof Error && error.message ? error.message : '正式提交失败，请重试'),
    )
  } finally {
    submitFlowPending.value = false
  }
}

async function refreshSubmission(): Promise<void> {
  try {
    await submission.pollNow()
  } catch {
    // The status card renders the store's recoverable error.
  }
}

async function retrySameSubmission(): Promise<void> {
  const draft = drafts.currentDraft
  if (!draft || submission.submitting) return
  try {
    await submission.submit(draft.id, draft.revision)
  } catch {
    // Keep the same persisted idempotency key; the status card explains the error.
  }
}

function formatDraftOption(draft: {
  id: string
  status: ReimbursementDraftStatus
  updatedAt: string
}): string {
  const timestamp = new Date(draft.updatedAt)
  const date = Number.isNaN(timestamp.getTime())
    ? draft.updatedAt
    : timestamp.toLocaleString('zh-CN', { hour12: false })
  return `${draftStatusLabels[draft.status]} · ${date} · ${draft.id.slice(0, 8)}`
}
</script>

<template>
  <main class="page-shell">
    <section
      class="hero"
      aria-labelledby="page-title"
    >
      <div>
        <p class="eyebrow">
          钉钉工作台应用
        </p>
        <h1 id="page-title">
          智能差旅报销
        </h1>
        <p class="summary">
          一次填写并上传原始材料，服务器生成最终 Excel 后直接发起钉钉 OA 审批。
        </p>
      </div>
      <el-tag
        :type="statusType"
        effect="light"
        round
        role="status"
        aria-live="polite"
      >
        {{ health.label }}
      </el-tag>
    </section>

    <el-card
      v-if="auth.status === 'loading' || auth.status === 'idle'"
      shadow="never"
      class="content-card"
      aria-busy="true"
    >
      <p
        class="visually-hidden"
        role="status"
        aria-live="polite"
      >
        正在确认登录状态
      </p>
      <el-skeleton
        :rows="4"
        animated
      />
    </el-card>
    <el-card
      v-else-if="auth.status === 'mock_required'"
      shadow="never"
      class="content-card"
      aria-live="polite"
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
      class="content-card narrow-card"
      aria-live="polite"
    >
      <template #header>
        <strong>选择本次报销部门</strong>
      </template>
      <p class="muted-copy">
        请从钉钉返回的合法部门中选择，部门名称不能手工填写。
      </p>
      <el-select
        v-model="departmentId"
        class="full-width"
        placeholder="请选择部门"
        aria-label="本次报销部门"
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
        :title="departmentId ? '' : '请先选择本次报销部门'"
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
          show-icon
          class="workspace-alert"
        >
          <template #default>
            <el-button
              link
              type="primary"
              @click="retryInitialization"
            >
              重新加载
            </el-button>
          </template>
        </el-alert>

        <el-card
          shadow="never"
          class="content-card reimbursement-card"
        >
          <template #header>
            <div class="card-header">
              <strong>报销草稿与基本信息</strong>
              <nav
                v-if="auth.isAdmin"
                class="admin-links"
                aria-label="管理员功能"
              >
                <RouterLink to="/admin/projects">
                  项目管理
                </RouterLink>
                <RouterLink to="/admin/settings">
                  系统设置
                </RouterLink>
              </nav>
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

          <div class="draft-toolbar">
            <el-select
              :model-value="currentDraftId"
              class="draft-select"
              placeholder="选择已有草稿"
              :loading="drafts.loadingDrafts || switchingDraft"
              :disabled="draftInteractionPending"
              aria-label="选择报销草稿"
              @update:model-value="chooseDraft"
            >
              <el-option
                v-for="draft in drafts.drafts"
                :key="draft.id"
                :label="formatDraftOption(draft)"
                :value="draft.id"
              />
            </el-select>
            <el-button
              :disabled="draftInteractionPending
                || creatingDraft
                || drafts.loadingReimbursementOptions"
              @click="requestNewDraft"
            >
              新建草稿
            </el-button>
          </div>

          <el-alert
            v-if="!drafts.currentDraft"
            title="先创建一份持久草稿"
            description="选择 OA 所属公司、OA 预算代码和 Excel 内部项目后即可创建；创建后再上传票据和填写费用。"
            type="info"
            :closable="false"
            show-icon
          >
            <template #default>
              <el-button
                type="primary"
                @click="requestNewDraft"
              >
                创建第一份草稿
              </el-button>
            </template>
          </el-alert>

          <template v-else>
            <div class="draft-status-row">
              <el-tag
                :type="drafts.currentDraft.status === 'DRAFT'
                  ? 'info'
                  : drafts.currentDraft.status === 'EXPIRED'
                    ? 'danger'
                    : 'warning'"
              >
                {{ draftStatusLabels[drafts.currentDraft.status] }}
              </el-tag>
              <span>草稿版本 {{ drafts.currentDraft.revision }}</span>
              <span
                v-if="formDirty"
                class="unsaved-indicator"
              >有未保存修改</span>
            </div>

            <fieldset
              class="plain-fieldset"
              :disabled="formReadOnly"
            >
              <div class="oa-option-grid">
                <el-form-item label="OA 所属公司">
                  <el-select
                    v-model="companyValue"
                    class="full-width"
                    placeholder="请选择 OA 所属公司"
                    aria-label="OA 所属公司"
                  >
                    <el-option
                      v-for="option in companyOptions"
                      :key="option.key ?? option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="OA 预算代码">
                  <el-select
                    v-model="budgetCodeValue"
                    class="full-width"
                    placeholder="请选择 OA 预算代码"
                    aria-label="OA 预算代码"
                  >
                    <el-option
                      v-for="option in budgetOptions"
                      :key="option.key ?? option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>
              </div>

              <section class="form-section project-section">
                <div class="section-heading">
                  <div>
                    <h2>报销 Excel 内部项目</h2>
                    <p>此字段只写入报销 Excel，与上面的 OA 预算代码是两个独立字段。</p>
                  </div>
                  <el-switch
                    v-model="expense.manualProject"
                    active-text="手工填写"
                    aria-label="切换为手工填写 Excel 内部项目"
                  />
                </div>
                <el-input
                  v-if="expense.manualProject"
                  v-model="expense.manualProjectText"
                  maxlength="255"
                  show-word-limit
                  placeholder="输入 Excel 内部项目名称"
                  aria-label="手工填写 Excel 内部项目"
                />
                <el-select
                  v-else
                  v-model="expense.selectedProjectId"
                  class="full-width"
                  filterable
                  remote
                  clearable
                  :remote-method="projects.search"
                  :loading="projects.loading"
                  placeholder="输入项目编号或名称搜索"
                  aria-label="搜索并选择 Excel 内部项目"
                  @visible-change="(open: boolean) => open && projects.search()"
                >
                  <el-option
                    v-if="expense.selectedProjectId
                      && !projects.options.some((project) => project.id === expense.selectedProjectId)"
                    :label="`已保存项目 #${expense.selectedProjectId}`"
                    :value="expense.selectedProjectId"
                  />
                  <el-option
                    v-for="project in projects.options"
                    :key="project.id"
                    :label="`${project.projectCode ? `${project.projectCode} · ` : ''}${project.projectName}`"
                    :value="project.id"
                  />
                </el-select>
                <p
                  v-if="!expense.manualProject && projects.errorMessage"
                  class="field-error"
                  role="alert"
                >
                  {{ projects.errorMessage }}
                  <el-button
                    link
                    type="primary"
                    @click="projects.search()"
                  >
                    重试
                  </el-button>
                </p>
              </section>
            </fieldset>

            <el-alert
              v-if="formReadOnlyReason"
              :title="formReadOnlyReason"
              type="info"
              :closable="false"
              show-icon
            />
          </template>
        </el-card>

        <template v-if="drafts.currentDraft">
          <fieldset
            class="editor-fieldset"
            :disabled="formReadOnly"
          >
            <TripSubsidyCard />
            <el-alert
              v-if="unresolvedOcrFiles.length > 0"
              :title="`${unresolvedOcrFiles.length} 张票据的 OCR 结果尚未决定`"
              description="请在票据列表中明确选择“添加到费用明细”或“忽略此票据”。决定全部票据后才能保存或提交。"
              type="warning"
              :closable="false"
              show-icon
              class="receipt-alert"
            />
            <ExpenseItemsCard
              :durable="true"
              :readonly="formReadOnly"
            />
          </fieldset>

          <TravelApprovalSelector
            v-model="selectedRelatedApprovals"
            :linked-approvals="drafts.currentDraft.relatedApprovals"
            :readonly="formReadOnly"
          />

          <ExpenseSummaryCard :preview-disabled-reason="previewDisabledReason" />

          <el-card
            shadow="never"
            class="content-card submission-card"
          >
            <div class="submission-actions">
              <div class="secondary-actions">
                <el-button
                  :loading="savingDraft"
                  :disabled="formReadOnly || drafts.busy || submitFlowPending"
                  @click="saveCurrentDraft"
                >
                  保存草稿
                </el-button>
                <span
                  v-if="formDirty"
                  class="unsaved-indicator"
                >有未保存修改</span>
                <span
                  v-else
                  class="saved-indicator"
                >当前内容已保存</span>
              </div>
              <div class="primary-submit-area">
                <p>提交后会正式发起钉钉审批；最终 Excel 和原始附件由服务器直接加入 OA。</p>
                <el-button
                  type="primary"
                  size="large"
                  :loading="submitFlowPending || submission.submitting"
                  :disabled="Boolean(submissionButtonReason)"
                  :title="submissionButtonReason"
                  @click="confirmAndSubmit"
                >
                  确认并提交到钉钉 OA
                </el-button>
              </div>
            </div>

            <el-alert
              v-if="drafts.mutationError && !submission.progressLabel"
              :title="drafts.mutationError"
              type="error"
              :closable="false"
              show-icon
              class="submission-status"
            />

            <section
              v-if="submission.activeDraftId === drafts.currentDraft.id
                && (submission.progressLabel || submission.errorMessage)"
              class="submission-status"
              aria-live="polite"
              data-testid="submission-status"
            >
              <el-alert
                :title="submission.progressLabel || '提交任务需要处理'"
                :description="submission.errorMessage || undefined"
                :type="submissionAlertType"
                :closable="false"
                show-icon
              />
              <el-progress
                :percentage="submissionProgressPercentage"
                :status="submission.status === 'SUBMITTED'
                  ? 'success'
                  : submission.status === 'FAILED_FINAL'
                    ? 'exception'
                    : undefined"
              />

              <dl
                v-if="submission.submission"
                class="submission-identifiers"
              >
                <div>
                  <dt>提交任务</dt>
                  <dd>{{ submission.submission.submissionId }}</dd>
                </div>
                <div v-if="submission.submission.processInstanceId">
                  <dt>钉钉流程实例</dt>
                  <dd>{{ submission.submission.processInstanceId }}</dd>
                </div>
                <div v-if="submission.submission.businessId">
                  <dt>审批编号</dt>
                  <dd>{{ submission.submission.businessId }}</dd>
                </div>
              </dl>

              <p
                v-if="submission.status === 'MANUAL_REVIEW'"
                class="manual-review-copy"
              >
                系统无法自动确认本次提交结果。请联系管理员，并提供上面的提交任务编号；不要重新发起报销。
              </p>
              <div class="submission-status-actions">
                <a
                  v-if="submission.status === 'SUBMITTED'
                    && submission.submission?.approvalUrl?.trim()"
                  :href="submission.submission.approvalUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                  data-testid="approval-link"
                >
                  <el-button type="primary">
                    打开钉钉 OA
                  </el-button>
                </a>
                <el-button
                  v-if="submission.submission && !submission.terminal"
                  :loading="submission.polling"
                  @click="refreshSubmission"
                >
                  刷新提交进度
                </el-button>
                <el-button
                  v-if="submission.requestError
                    && !submission.submission
                    && submission.idempotencyKey"
                  :loading="submission.submitting"
                  @click="retrySameSubmission"
                >
                  使用同一提交编号重试
                </el-button>
              </div>
            </section>
          </el-card>
        </template>

        <el-card
          v-else
          shadow="never"
          class="content-card no-draft-card"
        >
          <el-empty
            description="创建草稿后才能上传票据、填写费用并关联出差审批"
            :image-size="88"
          >
            <el-button
              type="primary"
              :disabled="companyOptions.length === 0 || budgetOptions.length === 0"
              @click="requestNewDraft"
            >
              新建报销草稿
            </el-button>
          </el-empty>
        </el-card>
      </template>
    </template>

    <el-card
      v-else
      shadow="never"
      class="content-card"
      aria-live="assertive"
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

    <el-dialog
      v-model="createDialogVisible"
      title="新建报销草稿"
      width="min(560px, calc(100% - 24px))"
      :close-on-click-modal="!creatingDraft"
      :close-on-press-escape="!creatingDraft"
      :show-close="!creatingDraft"
      destroy-on-close
    >
      <p class="muted-copy create-draft-intro">
        先确定三个基础字段。创建草稿后再上传票据和附件，不需要提前准备费用明细。
      </p>
      <el-form label-position="top">
        <el-form-item label="OA 所属公司">
          <el-select
            v-model="newCompanyValue"
            class="full-width"
            placeholder="请选择 OA 所属公司"
            aria-label="新草稿 OA 所属公司"
          >
            <el-option
              v-for="option in companyOptions"
              :key="option.key ?? option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="OA 预算代码">
          <el-select
            v-model="newBudgetCodeValue"
            class="full-width"
            placeholder="请选择 OA 预算代码"
            aria-label="新草稿 OA 预算代码"
          >
            <el-option
              v-for="option in budgetOptions"
              :key="option.key ?? option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="报销 Excel 内部项目">
          <el-radio-group v-model="newProjectMode">
            <el-radio value="selected">
              选择内部项目
            </el-radio>
            <el-radio value="manual">
              手工填写
            </el-radio>
          </el-radio-group>
          <p class="field-help full-width">
            Excel 内部项目与 OA 预算代码相互独立，请按实际用途填写。
          </p>
          <el-input
            v-if="newProjectMode === 'manual'"
            v-model="newManualProject"
            maxlength="255"
            show-word-limit
            placeholder="输入 Excel 内部项目名称"
            aria-label="新草稿手工 Excel 内部项目"
          />
          <el-select
            v-else
            v-model="newProjectId"
            class="full-width"
            filterable
            remote
            clearable
            :remote-method="projects.search"
            :loading="projects.loading"
            placeholder="输入项目编号或名称搜索"
            aria-label="新草稿选择 Excel 内部项目"
          >
            <el-option
              v-for="project in projects.options"
              :key="project.id"
              :label="`${project.projectCode ? `${project.projectCode} · ` : ''}${project.projectName}`"
              :value="project.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button
          :disabled="creatingDraft"
          @click="createDialogVisible = false"
        >
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="creatingDraft"
          :disabled="companyOptions.length === 0 || budgetOptions.length === 0"
          @click="createNewDraft"
        >
          创建草稿并开始填写
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.workspace-alert {
  margin-bottom: 18px;
}

.plain-fieldset,
.editor-fieldset {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.editor-fieldset:disabled {
  opacity: 0.72;
}

.draft-toolbar,
.draft-status-row,
.submission-actions,
.secondary-actions,
.submission-status-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 14px;
}

.draft-toolbar {
  margin-bottom: 20px;
}

.draft-select {
  min-width: 260px;
  flex: 1;
}

.draft-status-row {
  margin-bottom: 18px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.unsaved-indicator {
  color: var(--el-color-warning-dark-2);
  font-weight: 600;
}

.saved-indicator {
  color: var(--el-color-success-dark-2);
  font-size: 13px;
}

.oa-option-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 16px;
}

.project-section {
  margin-bottom: 6px;
}

.submission-card,
.no-draft-card {
  margin-top: 18px;
}

.submission-actions {
  justify-content: space-between;
  align-items: flex-end;
}

.primary-submit-area {
  display: grid;
  justify-items: end;
  max-width: 580px;
  gap: 8px;
}

.primary-submit-area p {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.5;
  text-align: right;
}

.submission-status {
  display: grid;
  gap: 14px;
  margin-top: 20px;
}

.submission-identifiers {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 14px;
  border-radius: 10px;
  background: var(--el-fill-color-light);
}

.submission-identifiers div {
  display: grid;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 10px;
}

.submission-identifiers dt {
  color: var(--el-text-color-secondary);
}

.submission-identifiers dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.manual-review-copy {
  margin: 0;
  color: var(--el-color-warning-dark-2);
  line-height: 1.6;
}

.create-draft-intro {
  margin-top: 0;
}

@media (max-width: 600px) {
  .oa-option-grid {
    grid-template-columns: 1fr;
  }

  .draft-toolbar,
  .submission-actions,
  .primary-submit-area {
    align-items: stretch;
    flex-direction: column;
  }

  .draft-select {
    width: 100%;
    min-width: 0;
  }

  .primary-submit-area {
    display: flex;
    max-width: none;
  }

  .primary-submit-area p {
    text-align: left;
  }

  .primary-submit-area .el-button,
  .secondary-actions .el-button {
    width: 100%;
    margin-left: 0;
  }

  .submission-identifiers div {
    grid-template-columns: 1fr;
    gap: 3px;
  }
}
</style>
