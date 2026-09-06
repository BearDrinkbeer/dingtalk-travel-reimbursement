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
const budgetLabel = computed(() => budgetOptions.value.find((option) => option.value === budgetCodeValue.value)?.label ?? '')
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
const canReplaceOutdatedForm = computed(() => templateMismatch.value && Boolean(drafts.currentDraft
  && ['DRAFT', 'REVIEW_READY'].includes(drafts.currentDraft.status))
  && !trackedSubmission.value && !submitFlowPending.value && !initializingWorkspace.value)
const formReadOnly = computed(() => !drafts.currentDraft || submitFlowPending.value
  || ['LOCKED', 'EXPIRED'].includes(drafts.currentDraft.status) || trackedSubmission.value
  || templateMismatch.value)
const formReadOnlyReason = computed(() => {
  if (!drafts.currentDraft) return '正在准备报销表单'
  if (submitFlowPending.value) return '正在提交，请稍候'
  if (drafts.currentDraft.status === 'EXPIRED') return '本次填写内容已过期，请重新填写'
  if (drafts.currentDraft.status === 'LOCKED' || trackedSubmission.value) return '本次报销已提交，内容已锁定'
  if (templateMismatch.value) return 'OA 表单已更新，请按新表单重新填写'
  return ''
})
const currentFormInput = computed<ReimbursementDraftInput>(() => ({
  ocrDispositionVersion: 1,
  companyValue: companyValue.value,
  budgetCodeValue: budgetCodeValue.value,
  trip: expense.tripPayload(),
  editingState: { includeSubsidy: expense.includeSubsidy, trip: { ...expense.trip } },
  items: expense.buildDraftExpenseItems(),
  dismissedOcrFileIds: [...expense.dismissedOcrFileIds],
}))
const inputDirty = computed(() => inputSignature(currentFormInput.value) !== savedInputSignature.value)
const relatedDirty = computed(() => relatedSignature(selectedRelatedApprovals.value) !== savedRelatedSignature.value)
const formDirty = computed(() => inputDirty.value || relatedDirty.value)
const saveLabel = computed(() => trackedSubmission.value || drafts.currentDraft?.status === 'LOCKED' ? '已提交'
  : saveError.value ? '保存失败，内容仍保留在本页'
  : saving.value ? '正在保存…' : formDirty.value ? '等待保存…' : '已保存')
const submissionButtonReason = computed(() => formReadOnlyReason.value || (drafts.busy ? '请等待材料处理完成' : ''))
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
    if (drafts.reimbursementOptionsError || drafts.listError) throw new Error(drafts.reimbursementOptionsError || drafts.listError)
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
    } else await createBlankReimbursement()
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
  if (disposed || initializingWorkspace.value || formReadOnly.value || !formDirty.value) return
  autosaveTimer = setTimeout(() => { void flushAutosave().catch(() => undefined) }, 600)
}
async function flushAutosave(): Promise<void> {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  if (savePromise) { await savePromise; return flushAutosave() }
  if (!drafts.currentDraft || !formDirty.value) return
  const draftId = drafts.currentDraft.id
  const scope = sessionScope()
  const run = async () => {
    saving.value = true
    saveError.value = ''
    try {
      // Keep typing enabled, save snapshots, and never hydrate an older response over live edits.
      while (formDirty.value && drafts.currentDraft?.id === draftId && sessionScope() === scope) {
        if (inputDirty.value) {
          const snapshot = JSON.parse(JSON.stringify(currentFormInput.value)) as ReimbursementDraftInput
          await drafts.saveDraft(snapshot)
          if (drafts.currentDraft?.id !== draftId || sessionScope() !== scope) return
          savedInputSignature.value = inputSignature(snapshot)
        }
        if (relatedDirty.value) {
          const selections = JSON.parse(JSON.stringify(selectedRelatedApprovals.value)) as ReimbursementRelatedApprovalSelection[]
          await drafts.saveRelatedApprovals(selections)
          if (drafts.currentDraft?.id !== draftId || sessionScope() !== scope) return
          savedRelatedSignature.value = relatedSignature(selections)
        }
      }
    } catch (error) {
      if (drafts.currentDraft?.id === draftId && sessionScope() === scope) {
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
  if (!selectedRelatedApprovals.value.length) return '请至少关联一张已通过的出差审批'
  if (!drafts.files.some((file) => file.status === 'ACTIVE')) return '请上传报销材料'
  for (const item of expense.items) {
    if ((item.requiresItinerary || item.transportType === 'ride_hailing') && !item.itineraryFileIds?.some((id) =>
      drafts.files.some((file) => file.id === id && file.status === 'ACTIVE' && file.role === 'ATTACHMENT_ONLY'),
    )) return `“${item.description || '网约车费用'}”缺少对应行程单，请点击编辑补齐`
    if (isForeignExpense(item) && !item.cnyAmountConfirmed) {
      return `请确认“${item.description || '国外票据'}”的人民币报销金额`
    }
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
onMounted(async () => { void health.check(); await auth.bootstrap(); await initializeWorkspace() })
watch(() => sessionScope(), () => {
  if (sessionScope()) void initializeWorkspace()
  else { initializedScope = ''; initializationScope = '' }
})
watch([currentFormInput, selectedRelatedApprovals], scheduleAutosave, { deep: true })
watch(budgetLabel, (label) => { expense.manualProject = true; expense.manualProjectText = label; expense.selectedProjectId = null })
watch(() => [expense.includeSubsidy, expense.trip, expense.items], () => {
  if (calculationTimer) clearTimeout(calculationTimer)
  calculationTimer = setTimeout(() => { void expense.refreshCalculations() }, 250)
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
          钉钉工作台应用
        </p>
        <h1 id="page-title">
          智能差旅报销
        </h1>
        <p class="summary">
          选预算、上传材料、核对费用，自动生成票据汇总和报销单并提交 OA。
        </p>
      </div>
      <el-tag
        :type="health.available === true ? 'success' : health.available === false ? 'danger' : 'info'"
        round
      >
        {{ health.label }}
      </el-tag>
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
          >
            <template #header>
              <div class="card-header">
                <strong>基本信息</strong><RouterLink
                  v-if="auth.isAdmin"
                  to="/admin/settings"
                >
                  系统设置
                </RouterLink>
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
            <fieldset
              class="plain-fieldset"
              :disabled="formReadOnly"
            >
              <div class="oa-option-grid">
                <el-form-item label="所属公司">
                  <el-select
                    v-model="companyValue"
                    class="full-width"
                    placeholder="请选择所属公司"
                    aria-label="所属公司"
                    :disabled="formReadOnly"
                  >
                    <el-option
                      v-for="option in companyOptions"
                      :key="option.key ?? option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="预算代码 / 项目">
                  <el-select
                    v-model="budgetCodeValue"
                    class="full-width"
                    filterable
                    placeholder="请选择钉钉预算代码"
                    aria-label="预算代码"
                    :disabled="formReadOnly"
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
              <p class="field-help">
                预算代码选项来自钉钉表单，所选完整名称会直接填入报销单 Excel 的项目栏。
              </p>
            </fieldset>
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
            :disabled="formReadOnly"
          >
            <TripSubsidyCard /><ExpenseItemsCard
              :durable="true"
              :readonly="formReadOnly"
            />
          </fieldset>
          <TravelApprovalSelector
            v-model="selectedRelatedApprovals"
            :linked-approvals="drafts.currentDraft.relatedApprovals"
            :readonly="formReadOnly"
          />
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
                <p>OA 附件为两个文件：票据汇总.pdf（含行程单）＋报销单.xlsx。</p>
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
                  :loading="submission.polling"
                  @click="submission.pollNow().catch(() => undefined)"
                >
                  刷新提交进度
                </el-button>
                <el-button
                  v-if="submission.requestError && !submission.submission && submission.idempotencyKey"
                  :loading="submission.submitting"
                  @click="retrySameSubmission"
                >
                  重试本次提交
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
.workspace-alert, .identity-grid { margin-bottom: 18px; }
.plain-fieldset, .editor-fieldset { min-width: 0; padding: 0; margin: 0; border: 0; }
.oa-option-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px; }
.submission-actions, .submission-status-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.primary-submit-area { text-align: right; }
.primary-submit-area p { color: var(--el-text-color-secondary); font-size: 13px; }
.submission-status { margin-top: 20px; }
.submission-status .el-progress { margin: 16px 0; }
@media (max-width: 640px) {
  .oa-option-grid { grid-template-columns: 1fr; gap: 0; }
  .primary-submit-area { text-align: left; }
}
</style>
