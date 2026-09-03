<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import ExpenseItemsCard from '@/components/reimbursement/ExpenseItemsCard.vue'
import ExpenseSummaryCard from '@/components/reimbursement/ExpenseSummaryCard.vue'
import TripSubsidyCard from '@/components/reimbursement/TripSubsidyCard.vue'
import { useAuthStore } from '@/stores/auth'
import { useExpenseStore } from '@/stores/expense'
import { useHealthStore } from '@/stores/health'
import { useProjectsStore } from '@/stores/projects'

const auth = useAuthStore()
const expense = useExpenseStore()
const health = useHealthStore()
const projects = useProjectsStore()
const departmentId = ref('')
let calculationTimer: ReturnType<typeof setTimeout> | undefined

const statusType = computed(() =>
  health.available === true ? 'success' : health.available === false ? 'danger' : 'info',
)

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
})

async function chooseDepartment(): Promise<void> {
  if (!departmentId.value) return
  await auth.selectDepartment(departmentId.value)
  await initializeForm()
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
            <div>
              <h2>报销项目 / 预算代码</h2>
              <p>可搜索项目，也可仅为本次报销手工填写。</p>
            </div>
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

      <TripSubsidyCard />
      <ExpenseItemsCard />
      <ExpenseSummaryCard />
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
  </main>
</template>
