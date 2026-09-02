<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import {
  excelDownloadErrorMessage,
  generateAndDownloadExpenseExcel,
} from '@/api/excel'
import { useAuthStore } from '@/stores/auth'
import { useExpenseStore } from '@/stores/expense'
import { useHealthStore } from '@/stores/health'
import { useProjectsStore } from '@/stores/projects'
import type { ExpenseCategoryId, ExpenseItem, TripType } from '@/types/expenses'
import type { ReceiptFileState, ReceiptFileStatus } from '@/types/receipts'
import { formatFileSize } from '@/utils/receiptFiles'

const auth = useAuthStore()
const expense = useExpenseStore()
const health = useHealthStore()
const projects = useProjectsStore()
const departmentId = ref('')
const editorVisible = ref(false)
const editorRevision = ref(0)
const excelDownloading = ref(false)
const receiptInput = ref<HTMLInputElement | null>(null)
const receiptPreviewVisible = ref(false)
const receiptPreviewUrl = ref('')
const receiptPreviewName = ref('')
const receiptPreviewKind = ref<'image' | 'pdf'>('image')
const editor = reactive({
  id: '', category: '' as ExpenseCategoryId, date: '', description: '', amount: '', receiptCount: 1,
})
let calculationTimer: ReturnType<typeof setTimeout> | undefined

const tripTypes: Array<{ id: TripType; name: string }> = [
  { id: 'business', name: '商务出差' },
  { id: 'project', name: '市外项目' },
  { id: 'same_city_project', name: '同市项目' },
  { id: 'internal', name: '公司内部出差' },
]
const categoryNames = computed<Record<string, string>>(() =>
  Object.fromEntries(expense.categories.map((item) => [item.id, item.name])),
)
const unlinkedReceiptFiles = computed(() =>
  expense.receiptFiles.filter((receipt) => !receipt.ocrItemId),
)
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
const canAddExpenseItem = computed(
  () =>
    expense.items.length < expense.maxExpenseItems &&
    expense.manualCategories.length > 0 &&
    !expense.categoryLoadError,
)
const receiptUploadDisabledReason = computed(() => {
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
  if (expense.items.length >= expense.maxExpenseItems) {
    return `费用明细已达到 ${expense.maxExpenseItems} 条上限`
  }
  if (expense.categoriesLoading) return '费用类别正在加载'
  if (expense.categoryLoadError) return expense.categoryLoadError
  if (!expense.manualCategories.length) return '暂无可手工选择的费用类别'
  return ''
})
const statusType = computed(() =>
  health.available === true ? 'success' : health.available === false ? 'danger' : 'info',
)
const excelDisabledReason = computed(() => {
  if (!auth.session?.selectedDepartment) return '请先选择本次报销部门'
  return expense.excelDisabledReason
})

async function initializeForm(): Promise<void> {
  await Promise.all([projects.search(), expense.loadCategories()])
}

onMounted(async () => {
  void health.check()
  await auth.bootstrap()
  if (auth.status === 'authenticated') await initializeForm()
})

watch(() => auth.status, (status) => {
  if (status === 'authenticated') void initializeForm()
})
watch(
  () => [
    expense.includeSubsidy,
    expense.trip.tripType, expense.trip.startDate, expense.trip.startTime,
    expense.trip.endDate, expense.trip.endTime, expense.trip.policyConfirmed,
    expense.trip.confirmedEffectiveDays, expense.trip.noSubsidyException,
    expense.items
      .map((item) => [
        item.id, item.category, item.date, item.displayDate,
        item.description, item.amount, item.receiptCount,
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
  releaseReceiptPreview()
})

async function chooseDepartment(): Promise<void> {
  if (!departmentId.value) return
  await auth.selectDepartment(departmentId.value)
  await initializeForm()
}

function openNewItem(): void {
  const firstCategory = expense.manualCategories[0]
  if (!firstCategory) {
    ElMessage.error(expense.categoryLoadError || '费用类别尚未加载，请稍后重试')
    return
  }
  Object.assign(editor, {
    id: '', category: firstCategory.id, date: '',
    description: '', amount: '', receiptCount: 1,
  })
  editorRevision.value += 1
  editorVisible.value = true
}

function openEditItem(item: ExpenseItem): void {
  Object.assign(editor, {
    id: item.id, category: item.category, date: item.date ?? '',
    description: item.description, amount: item.amount, receiptCount: item.receiptCount,
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

function readableWarning(warning: string): string {
  return warningLabels[warning] ?? '请核对识别结果'
}

function chooseReceiptFiles(): void {
  receiptInput.value?.click()
}

function releaseReceiptPreview(): void {
  if (receiptPreviewUrl.value) URL.revokeObjectURL(receiptPreviewUrl.value)
  receiptPreviewUrl.value = ''
}

function openReceiptPreview(receipt: ReceiptFileState): void {
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
  if (!files.length) return
  const result = await expense.addReceiptFiles(files)
  for (const rejection of result.rejected.slice(0, 3)) {
    ElMessage.warning(`${rejection.file.name}：${rejection.message}`)
  }
  if (result.rejected.length > 3) {
    ElMessage.warning(`另有 ${result.rejected.length - 3} 个文件未加入，请检查数量和大小`)
  }
}

async function removeReceipt(localId: string): Promise<void> {
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
  const receipt = expense.receiptByItemId(id)
  if (receipt) {
    await removeReceipt(receipt.localId)
    return
  }
  expense.removeItem(id)
  void expense.refreshCalculations()
}

async function retryItemRecognition(id: string): Promise<void> {
  const receipt = expense.receiptByItemId(id)
  if (!receipt) return
  await expense.retryReceipt(receipt.localId)
  if (receipt.status === 'done' && !receipt.error) ElMessage.success('已用新的 OCR 结果更新明细')
  void expense.refreshCalculations()
}

async function downloadExcel(): Promise<void> {
  const payload = expense.buildExcelPayload()
  if (!payload || excelDisabledReason.value) {
    ElMessage.warning(excelDisabledReason.value || '请先完成报销信息')
    return
  }
  excelDownloading.value = true
  try {
    await generateAndDownloadExpenseExcel(payload)
    ElMessage.success('Excel 已生成并开始下载')
  } catch (error) {
    ElMessage.error(await excelDownloadErrorMessage(error))
  } finally {
    excelDownloading.value = false
  }
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
          差旅报销单生成
        </h1>
        <p class="summary">
          身份由公司钉钉确认，当前报销内容仅保存在本页内存中。
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
        shadow="never"
        class="content-card reimbursement-card"
      >
        <template #header>
          <div class="card-header">
            <strong>员工与项目</strong>
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
        <section class="form-section">
          <div class="section-heading">
            <div><h2>报销项目 / 预算代码</h2><p>可搜索项目，也可仅为本次报销手工填写。</p></div>
            <el-switch
              v-model="expense.manualProject"
              active-text="手工填写"
              aria-label="切换为手工填写报销项目"
            />
          </div>
          <el-input
            v-if="expense.manualProject"
            v-model="expense.manualProjectText"
            maxlength="255"
            show-word-limit
            placeholder="输入项目或预算代码说明"
            aria-label="手工报销项目或预算代码"
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
            aria-label="搜索并选择报销项目或预算代码"
            @visible-change="(open: boolean) => open && projects.search()"
          >
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
      </el-card>

      <el-card
        shadow="never"
        class="content-card reimbursement-card"
      >
        <template #header>
          <strong>出差补助（可选）</strong>
        </template>
        <el-form label-position="top">
          <el-form-item label="是否申请出差补助">
            <el-switch
              :model-value="expense.includeSubsidy"
              active-text="申请出差补助"
              inactive-text="不申请"
              @update:model-value="expense.setSubsidyIncluded"
            />
          </el-form-item>
          <el-alert
            v-if="!expense.includeSubsidy"
            title="本报销单不申请出差补助"
            description="无需填写出差类型和出发、返回时间；系统只汇总费用明细。"
            type="info"
            :closable="false"
          />
          <template v-else>
            <el-form-item label="出差类型">
              <el-select
                :model-value="expense.trip.tripType"
                class="full-width"
                @update:model-value="expense.setTripType"
              >
                <el-option
                  v-for="type in tripTypes"
                  :key="type.id"
                  :label="type.name"
                  :value="type.id"
                />
              </el-select>
            </el-form-item>
            <div class="trip-grid">
              <el-form-item label="出发日期">
                <el-date-picker
                  v-model="expense.trip.startDate"
                  type="date"
                  value-format="YYYY-MM-DD"
                  class="full-width"
                />
              </el-form-item>
              <el-form-item label="出发时间">
                <el-time-picker
                  v-model="expense.trip.startTime"
                  format="HH:mm"
                  value-format="HH:mm"
                  class="full-width"
                />
              </el-form-item>
              <el-form-item label="返回日期">
                <el-date-picker
                  v-model="expense.trip.endDate"
                  type="date"
                  value-format="YYYY-MM-DD"
                  class="full-width"
                />
              </el-form-item>
              <el-form-item label="返回时间">
                <el-time-picker
                  v-model="expense.trip.endTime"
                  format="HH:mm"
                  value-format="HH:mm"
                  class="full-width"
                />
              </el-form-item>
            </div>
            <el-alert
              v-if="expense.trip.tripType === 'project' && expense.projectPolicyType"
              :title="expense.projectPolicyType === 'long_term_project'
                ? `系统识别为市外长期项目（${expense.projectCalendarDays} 个自然日）`
                : `系统识别为市外短期项目（${expense.projectCalendarDays} 个自然日）`"
              :description="expense.projectPolicyType === 'long_term_project'
                ? '超过 30 个自然日，使用长期标准并按半天规则自动计算。'
                : '30 个自然日以内（含），使用短期标准并自动计算。'"
              type="info"
              :closable="false"
              class="calculation-alert"
            />
            <el-alert
              v-if="!expense.requiresPolicyConfirmation"
              title="自动计算规则"
              description="出发日 12:00 前计 1 天、12:00（含）以后计 0.5 天；返回日 12:00 前计 0.5 天、12:00（含）以后计 1 天。同日从 12:00 前跨至 12:00（含）以后计 1 天，否则计 0.5 天。"
              type="info"
              :closable="false"
            />
            <div
              v-else
              class="policy-confirmation"
            >
              <el-alert
                title="该类型不自动推断政策例外"
                description="请按公司现行制度确认本次有效天数；每日标准由管理员统一配置，员工不能修改。"
                type="warning"
                :closable="false"
              />
              <div class="policy-fields">
                <el-form-item label="确认有效天数（0.5 天为单位）">
                  <el-input
                    v-model="expense.trip.confirmedEffectiveDays"
                    inputmode="decimal"
                    placeholder="例如 8.0"
                    maxlength="5"
                  />
                </el-form-item>
              </div>
              <el-checkbox v-model="expense.trip.policyConfirmed">
                我已按公司现行制度确认以上有效天数
              </el-checkbox>
              <el-checkbox
                v-if="expense.trip.tripType === 'internal'"
                v-model="expense.trip.noSubsidyException"
              >
                本次适用不补助例外（例如无锡—苏州）
              </el-checkbox>
              <p
                v-if="expense.policyInputError"
                class="field-error"
              >
                {{ expense.policyInputError }}
              </p>
            </div>
          </template>
          <el-alert
            v-if="expense.calculationError"
            :title="expense.calculationError"
            type="error"
            :closable="false"
            class="calculation-alert"
            role="alert"
          />
          <div
            v-if="expense.includeSubsidy && expense.totals?.subsidy"
            class="subsidy-preview"
            role="status"
            aria-live="polite"
          >
            <span>自然日 {{ expense.totals.subsidy.calendarDays }} 天</span>
            <span>有效 {{ expense.totals.subsidy.effectiveDays }} 天</span>
            <span>¥{{ expense.totals.subsidy.dailyRate }} / 天</span>
            <strong>补助 ¥{{ expense.totals.subsidy.total }}</strong>
          </div>
        </el-form>
      </el-card>

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
                :loading="expense.receiptBusy"
                :disabled="Boolean(receiptUploadDisabledReason)"
                :title="receiptUploadDisabledReason"
                @click="chooseReceiptFiles"
              >
                选择票据文件
              </el-button>
            </div>
            <input
              ref="receiptInput"
              class="visually-hidden"
              type="file"
              accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
              multiple
              :disabled="expense.receiptBusy"
              @change="onReceiptSelection"
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
          title="一个文件只能放一张票据"
          description="支持一次选择多个 JPG、JPEG、PNG 和单页 PDF。OCR 结果会直接成为可编辑的费用条目；不支持多页汇总 PDF、行程单或文件合并。临时文件由后台自动清理。"
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
        <div
          v-if="unlinkedReceiptFiles.length"
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
                :disabled="expense.receiptBusy"
                @click="expense.retryReceipt(receipt.localId)"
              >
                重新识别
              </el-button>
              <el-button
                v-else-if="receipt.status === 'failed'"
                link
                type="primary"
                :disabled="expense.receiptBusy"
                @click="expense.retryReceipt(receipt.localId)"
              >
                重试上传
              </el-button>
              <el-button
                link
                type="danger"
                :disabled="expense.receiptBusy"
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
            @click="expense.loadCategories(true)"
          >
            重新加载费用类别
          </el-button>
        </div>
        <el-empty
          v-if="expense.items.length === 0 && unlinkedReceiptFiles.length === 0"
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
                  v-if="expense.receiptByItemId(scope.row.id)?.name"
                  type="button"
                  class="receipt-file-preview-link receipt-meta"
                  :aria-label="`预览票据 ${expense.receiptByItemId(scope.row.id)?.name}`"
                  @click="previewItemReceipt(scope.row.id)"
                >
                  {{ expense.receiptByItemId(scope.row.id)?.name }} · 预览
                </button>
                <span
                  v-if="scope.row.warnings?.length"
                  class="ocr-warning"
                >
                  {{ scope.row.warnings.map(readableWarning).join('、') }}
                </span>
                <span
                  v-if="expense.receiptByItemId(scope.row.id)?.error"
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
                @click="openEditItem(scope.row)"
              >
                编辑
              </el-button>
              <el-button
                v-if="expense.receiptByItemId(scope.row.id)?.tempId"
                link
                type="primary"
                :disabled="expense.receiptBusy"
                @click="retryItemRecognition(scope.row.id)"
              >
                重新识别
              </el-button>
              <el-button
                link
                type="danger"
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
            <p>{{ item.displayDate || '日期待补充' }} · {{ item.receiptCount }} 张</p><p>{{ item.description }}</p>
            <p
              v-if="item.source === 'ocr'"
              class="ocr-warning"
            >
              OCR<template v-if="item.warnings?.length">
                · {{ item.warnings.map(readableWarning).join('、') }}
              </template>
            </p>
            <p v-if="expense.receiptByItemId(item.id)?.name">
              <button
                type="button"
                class="receipt-file-preview-link"
                :aria-label="`预览票据 ${expense.receiptByItemId(item.id)?.name}`"
                @click="previewItemReceipt(item.id)"
              >
                {{ expense.receiptByItemId(item.id)?.name }} · 预览
              </button>
            </p>
            <p
              v-if="expense.receiptByItemId(item.id)?.error"
              class="field-error"
            >
              {{ expense.receiptByItemId(item.id)?.error }}
            </p>
            <div class="mobile-actions">
              <el-button
                size="small"
                @click="openEditItem(item)"
              >
                编辑
              </el-button>
              <el-button
                v-if="expense.receiptByItemId(item.id)?.tempId"
                size="small"
                :disabled="expense.receiptBusy"
                @click="retryItemRecognition(item.id)"
              >
                重新识别
              </el-button>
              <el-button
                size="small"
                type="danger"
                plain
                @click="removeItem(item.id)"
              >
                删除
              </el-button>
            </div>
          </article>
        </div>
      </el-card>

      <el-card
        v-loading="expense.calculating"
        shadow="never"
        class="content-card summary-card"
      >
        <div class="totals-grid">
          <div><span>票据金额</span><strong>¥{{ expense.displayExpenseTotal }}</strong></div>
          <div><span>出差补助</span><strong>¥{{ expense.displaySubsidyTotal }}</strong></div>
          <div><span>票据张数</span><strong>{{ expense.displayReceiptCount }} 张</strong></div>
          <div class="grand-total">
            <span>合计</span><strong>¥{{ expense.displayTotal }}</strong>
          </div>
        </div>
        <p class="uppercase-amount">
          人民币大写：{{ expense.totals?.uppercaseAmount ?? '待服务端计算' }}
        </p>
        <div class="excel-download-action">
          <el-button
            type="primary"
            size="large"
            :loading="excelDownloading"
            :disabled="Boolean(excelDisabledReason)"
            @click="downloadExcel"
          >
            生成并下载 Excel
          </el-button>
          <p
            v-if="excelDisabledReason"
            class="field-error"
          >
            {{ excelDisabledReason }}
          </p>
        </div>
      </el-card>
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
      <el-form label-position="top">
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
          @click="saveItem"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>
