<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'
import type {
  OaTravelApproval,
  ReimbursementRelatedApproval,
  ReimbursementRelatedApprovalSelection,
} from '@/types/reimbursements'

const props = withDefaults(defineProps<{
  modelValue: ReimbursementRelatedApprovalSelection[]
  linkedApprovals?: ReimbursementRelatedApproval[]
  readonly?: boolean
}>(), {
  linkedApprovals: () => [],
  readonly: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: ReimbursementRelatedApprovalSelection[]]
}>()

interface ApprovalRow {
  processInstanceId: string
  profileKey: string
  profileDisplayName: string
  title: string
  businessId: string
  startDate: string
  endDate: string
  linkedOnly: boolean
}

const drafts = useReimbursementDraftStore()
const fromDate = ref('')
const toDate = ref('')
const keyword = ref('')
const localError = ref('')
const candidateCache = new Map<string, OaTravelApproval>()

const selectedIds = computed(() => new Set(
  props.modelValue.map((selection) => selection.processInstanceId),
))
const profileLabels = computed<Record<string, string>>(() => Object.fromEntries(
  (drafts.reimbursementOptions?.travelProfiles ?? []).map((profile) => [
    profile.profileKey,
    profile.displayName,
  ]),
))
const rows = computed<ApprovalRow[]>(() => {
  const result: ApprovalRow[] = drafts.travelApprovals.map((approval) => ({
    processInstanceId: approval.processInstanceId,
    profileKey: approval.profileKey,
    profileDisplayName: approval.profileDisplayName,
    title: approval.title,
    businessId: approval.businessId,
    startDate: approval.startDate,
    endDate: approval.endDate,
    linkedOnly: false,
  }))
  const present = new Set(result.map((row) => row.processInstanceId))
  for (const approval of props.linkedApprovals) {
    if (present.has(approval.processInstanceId)) continue
    result.push({
      processInstanceId: approval.processInstanceId,
      profileKey: approval.profileKey,
      profileDisplayName: profileLabels.value[approval.profileKey] ?? approval.profileKey,
      title: approval.title,
      businessId: approval.businessId,
      startDate: approval.startDate,
      endDate: approval.endDate,
      linkedOnly: true,
    })
    present.add(approval.processInstanceId)
  }
  for (const selection of props.modelValue) {
    if (present.has(selection.processInstanceId)) continue
    const approval = candidateCache.get(selection.processInstanceId)
    if (!approval) continue
    result.push({
      processInstanceId: approval.processInstanceId,
      profileKey: approval.profileKey,
      profileDisplayName: approval.profileDisplayName,
      title: approval.title,
      businessId: approval.businessId,
      startDate: approval.startDate,
      endDate: approval.endDate,
      linkedOnly: false,
    })
  }
  return result
})

watch(
  () => drafts.travelApprovals,
  (approvals) => {
    for (const approval of approvals) candidateCache.set(approval.processInstanceId, approval)
  },
  { immediate: true },
)

onMounted(() => {
  if (
    drafts.travelApprovals.length === 0
    && !drafts.loadingTravelApprovals
    && !drafts.travelApprovalsError
  ) void search()
})

async function search(): Promise<void> {
  if (props.readonly) return
  localError.value = ''
  const hasFrom = Boolean(fromDate.value)
  const hasTo = Boolean(toDate.value)
  if (hasFrom !== hasTo) {
    localError.value = '自定义日期范围需要同时填写开始和结束日期'
    return
  }
  if (hasFrom && fromDate.value > toDate.value) {
    localError.value = '开始日期不能晚于结束日期'
    return
  }
  await drafts.loadTravelApprovals({
    ...(hasFrom ? { from: fromDate.value, to: toDate.value } : {}),
    ...(keyword.value.trim() ? { query: keyword.value.trim() } : {}),
  })
}

function selectionForRow(row: ApprovalRow): ReimbursementRelatedApprovalSelection | null {
  const existing = props.modelValue.find(
    (selection) => selection.processInstanceId === row.processInstanceId,
  )
  if (existing) return existing
  const candidate = candidateCache.get(row.processInstanceId)
  const queryWindow = drafts.travelApprovalQueryWindow
  if (!candidate || !queryWindow) return null
  return {
    processInstanceId: candidate.processInstanceId,
    profileKey: candidate.profileKey,
    queryWindow: { ...queryWindow },
  }
}

function toggle(row: ApprovalRow, checked: boolean): void {
  if (props.readonly) return
  localError.value = ''
  if (!checked) {
    emit(
      'update:modelValue',
      props.modelValue.filter(
        (selection) => selection.processInstanceId !== row.processInstanceId,
      ),
    )
    return
  }
  const selection = selectionForRow(row)
  if (!selection) {
    localError.value = '该审批的查询凭据已失效，请重新加载后再选择'
    return
  }
  emit('update:modelValue', [...props.modelValue, selection])
}
</script>

<template>
  <el-card
    shadow="never"
    class="content-card reimbursement-card travel-approval-card"
  >
    <template #header>
      <div class="card-header">
        <div>
          <strong>关联已通过的出差审批</strong>
          <span class="section-note">可关联多张；系统只显示当前员工发起且已通过的审批</span>
        </div>
        <el-tag
          :type="modelValue.length ? 'success' : 'warning'"
          effect="light"
        >
          已选 {{ modelValue.length }} 张
        </el-tag>
      </div>
    </template>

    <fieldset
      class="plain-fieldset"
      :disabled="readonly"
    >
      <div class="travel-query-grid">
        <el-date-picker
          v-model="fromDate"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="查询开始日期"
          aria-label="出差审批查询开始日期"
          class="full-width"
        />
        <el-date-picker
          v-model="toDate"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="查询结束日期"
          aria-label="出差审批查询结束日期"
          class="full-width"
        />
        <el-input
          v-model="keyword"
          clearable
          maxlength="100"
          placeholder="标题或审批编号"
          aria-label="搜索出差审批"
          @keyup.enter="search"
        />
        <el-button
          :loading="drafts.loadingTravelApprovals"
          @click="search"
        >
          查询审批
        </el-button>
      </div>
    </fieldset>

    <el-alert
      v-if="readonly"
      title="当前报销已进入提交阶段，关联审批不可再修改"
      type="info"
      :closable="false"
      class="travel-query-alert"
    />
    <el-alert
      v-else-if="localError || drafts.travelApprovalsError"
      :title="localError || drafts.travelApprovalsError"
      type="error"
      :closable="false"
      show-icon
      class="travel-query-alert"
    >
      <template #default>
        <el-button
          v-if="drafts.travelApprovalsError"
          link
          type="primary"
          @click="search"
        >
          重新加载
        </el-button>
      </template>
    </el-alert>

    <el-skeleton
      v-if="drafts.loadingTravelApprovals && rows.length === 0"
      :rows="3"
      animated
      class="travel-result-loading"
    />
    <el-empty
      v-else-if="rows.length === 0"
      description="当前查询范围内没有已通过的出差审批"
      :image-size="72"
    >
      <el-button
        v-if="!readonly"
        :loading="drafts.loadingTravelApprovals"
        @click="search"
      >
        重新加载
      </el-button>
    </el-empty>
    <div
      v-else
      class="travel-approval-list"
      aria-label="可关联的出差审批"
      aria-live="polite"
    >
      <article
        v-for="row in rows"
        :key="row.processInstanceId"
        class="travel-approval-row"
        :class="{ 'is-selected': selectedIds.has(row.processInstanceId) }"
      >
        <el-checkbox
          :model-value="selectedIds.has(row.processInstanceId)"
          :disabled="readonly"
          :aria-label="`选择出差审批 ${row.title}`"
          @change="(checked: boolean) => toggle(row, checked)"
        />
        <span class="travel-approval-main">
          <span class="travel-approval-title">
            <strong>{{ row.title }}</strong>
            <el-tag
              v-if="row.linkedOnly"
              size="small"
              type="info"
            >
              已关联
            </el-tag>
          </span>
          <span>{{ row.startDate }} 至 {{ row.endDate }} · {{ row.profileDisplayName }}</span>
          <small>审批编号：{{ row.businessId }}</small>
        </span>
      </article>
    </div>

    <p class="field-help">
      默认查询近期审批；需要更早记录时设置日期范围。选中的审批会自动保存并由服务器重新核验。
    </p>
  </el-card>
</template>

<style scoped>
.plain-fieldset {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

.travel-query-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr)) minmax(180px, 1.2fr) auto;
  gap: 10px;
}

.travel-query-alert,
.travel-result-loading {
  margin-top: 14px;
}

.travel-approval-list {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}

.travel-approval-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 13px 14px;
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
}

.travel-approval-row.is-selected {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
}

.travel-approval-main {
  display: grid;
  min-width: 0;
  gap: 5px;
  color: var(--el-text-color-regular);
  line-height: 1.45;
}

.travel-approval-main small {
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}

.travel-approval-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-primary);
}

@media (max-width: 720px) {
  .travel-query-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 480px) {
  .travel-query-grid {
    grid-template-columns: 1fr;
  }
}
</style>
