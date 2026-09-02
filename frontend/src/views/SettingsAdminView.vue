<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import { getExpenseCategories } from '@/api/expenses'
import {
  createReceiptKeyword,
  deleteReceiptKeyword,
  listReceiptKeywords,
  updateReceiptKeyword,
} from '@/api/receiptKeywords'
import {
  getExpenseSettings,
  normalizeSubsidyRate,
  updateExpenseSettings,
} from '@/api/settings'
import { apiErrorMessage } from '@/api/errors'
import type { ExpenseCategoryMetadata, SubsidyRateType } from '@/types/expenses'
import type { ReceiptKeywordMapping } from '@/api/receiptKeywords'

const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const keywordMappings = ref<ReceiptKeywordMapping[]>([])
const keywordCategories = ref<ExpenseCategoryMetadata[]>([])
const keywordLoading = ref(false)
const keywordSaving = ref(false)
const keywordLoadError = ref('')
const keywordDialogOpen = ref(false)
const editingKeywordId = ref<number | null>(null)
const keywordValidationError = ref('')
const keywordForm = reactive({ keyword: '', categoryId: '' })
const form = reactive({
  subsidyRates: {
    business: '100.00',
    short_term_project: '100.00',
    long_term_project: '150.00',
    same_city_project: '50.00',
    internal: '100.00',
  },
  calculationMode: 'half_day_12' as const,
})
const rateFields: Array<{ id: SubsidyRateType; label: string }> = [
  { id: 'business', label: '商务出差' },
  { id: 'short_term_project', label: '市外项目短期' },
  { id: 'long_term_project', label: '市外项目长期' },
  { id: 'same_city_project', label: '同市项目' },
  { id: 'internal', label: '公司内部出差' },
]
const configurableKeywordCategories = computed(() =>
  keywordCategories.value.filter((category) => category.id !== 'other'),
)
const keywordGroups = computed(() =>
  keywordCategories.value.map((category) => ({
    category,
    mappings: keywordMappings.value.filter(
      (mapping) => mapping.categoryId === category.id,
    ),
  })),
)
const keywordDialogTitle = computed(() => {
  if (editingKeywordId.value !== null) return '编辑分类关键词'
  const category = keywordCategories.value.find(
    (item) => item.id === keywordForm.categoryId,
  )
  return category ? `为${category.name}添加关键词` : '新增分类关键词'
})

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    Object.assign(form, await getExpenseSettings())
  } catch (error) {
    loadError.value = apiErrorMessage(error, '系统设置加载失败，请重试')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  for (const field of rateFields) {
    const normalized = normalizeSubsidyRate(form.subsidyRates[field.id])
    if (normalized === null) {
      ElMessage.error(`${field.label}必须大于 0 且不超过 10000 元，最多两位小数`)
      return
    }
    form.subsidyRates[field.id] = normalized
  }
  saving.value = true
  try {
    Object.assign(form, await updateExpenseSettings(form))
    ElMessage.success('系统设置已保存')
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '系统设置保存失败，请重试'))
  } finally {
    saving.value = false
  }
}

async function loadKeywords(): Promise<void> {
  keywordLoading.value = true
  keywordLoadError.value = ''
  try {
    const [mappings, categories] = await Promise.all([
      listReceiptKeywords(),
      getExpenseCategories(),
    ])
    keywordMappings.value = mappings
    keywordCategories.value = categories.filter((category) => category.manualSelectable)
  } catch (error) {
    keywordMappings.value = []
    keywordCategories.value = []
    keywordLoadError.value = apiErrorMessage(error, '分类关键词加载失败，请重试')
  } finally {
    keywordLoading.value = false
  }
}

function openKeywordCreate(categoryId: string): void {
  editingKeywordId.value = null
  keywordValidationError.value = ''
  Object.assign(keywordForm, {
    keyword: '',
    categoryId,
  })
  keywordDialogOpen.value = true
}

function openKeywordEdit(mapping: ReceiptKeywordMapping): void {
  editingKeywordId.value = mapping.id
  keywordValidationError.value = ''
  Object.assign(keywordForm, {
    keyword: mapping.keyword,
    categoryId: mapping.categoryId,
  })
  keywordDialogOpen.value = true
}

async function saveKeyword(): Promise<void> {
  const keyword = keywordForm.keyword.trim().replace(/\s+/g, ' ')
  if (keyword.length < 2) {
    keywordValidationError.value = /^\d$/.test(keyword)
      ? '单个数字容易误匹配日期、金额和票据号码，请填写至少 2 个字符'
      : '关键词至少填写 2 个字符'
    return
  }
  keywordValidationError.value = ''
  if (!configurableKeywordCategories.value.some(
    (category) => category.id === keywordForm.categoryId,
  )) {
    ElMessage.warning('请选择费用类别')
    return
  }
  keywordSaving.value = true
  try {
    const input = { keyword, categoryId: keywordForm.categoryId }
    if (editingKeywordId.value === null) await createReceiptKeyword(input)
    else await updateReceiptKeyword(editingKeywordId.value, input)
    keywordDialogOpen.value = false
    ElMessage.success('分类关键词已保存')
    await loadKeywords()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '分类关键词保存失败，请重试'))
  } finally {
    keywordSaving.value = false
  }
}

async function removeKeyword(mapping: ReceiptKeywordMapping): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除关键词“${mapping.keyword}”吗？`, '删除分类关键词', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      showClose: false,
      closeOnClickModal: false,
    })
    await deleteReceiptKeyword(mapping.id)
    ElMessage.success('分类关键词已删除')
    await loadKeywords()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '分类关键词删除失败，请重试'))
  }
}

onMounted(() => {
  void Promise.all([load(), loadKeywords()])
})
</script>

<template>
  <main class="page-shell narrow-page">
    <div class="admin-page-header">
      <div>
        <nav
          class="admin-links admin-page-nav"
          aria-label="管理员功能"
        >
          <RouterLink to="/">
            报销单
          </RouterLink>
          <RouterLink to="/admin/projects">
            项目管理
          </RouterLink>
          <RouterLink
            to="/admin/settings"
            aria-current="page"
          >
            系统设置
          </RouterLink>
        </nav>
        <h1>系统设置</h1>
      </div>
    </div>
    <el-card
      v-loading="keywordLoading"
      shadow="never"
    >
      <div class="settings-keyword-heading">
        <div>
          <h2>票据分类关键词</h2>
          <p>按费用类别维护关键词；点击关键词可修改，点击 × 可删除。</p>
          <p>不同类别同时命中时保留为“其他”；专用票据仍会结合版式结构识别。</p>
        </div>
      </div>
      <el-alert
        v-if="keywordLoadError"
        :title="keywordLoadError"
        type="error"
        show-icon
        :closable="false"
        class="admin-load-error"
      >
        <template #default>
          <el-button
            link
            type="primary"
            @click="loadKeywords"
          >
            重新加载
          </el-button>
        </template>
      </el-alert>
      <div
        v-else
        class="settings-keyword-groups"
      >
        <section
          v-for="group in keywordGroups"
          :key="group.category.id"
          class="settings-keyword-group"
          :aria-label="`${group.category.name}分类关键词`"
        >
          <strong>{{ group.category.name }}</strong>
          <div class="settings-keyword-tags">
            <template v-if="group.category.id === 'other'">
              <span class="settings-keyword-empty">未命中或冲突时自动归入，无需配置关键词</span>
            </template>
            <template v-else-if="group.mappings.length > 0">
              <el-tag
                v-for="mapping in group.mappings"
                :key="mapping.id"
                closable
                disable-transitions
                class="settings-keyword-tag"
                title="点击修改关键词"
                @click="openKeywordEdit(mapping)"
                @close.stop="removeKeyword(mapping)"
              >
                {{ mapping.keyword }}
              </el-tag>
            </template>
            <span
              v-else
              class="settings-keyword-empty"
            >暂无关键词</span>
          </div>
          <el-button
            v-if="group.category.id !== 'other'"
            link
            type="primary"
            @click="openKeywordCreate(group.category.id)"
          >
            添加
          </el-button>
        </section>
      </div>
    </el-card>

    <el-card
      v-loading="loading"
      shadow="never"
      class="settings-subsidy-card"
    >
      <el-alert
        v-if="loadError"
        :title="loadError"
        type="error"
        show-icon
        :closable="false"
        class="admin-load-error"
      >
        <template #default>
          <el-button
            link
            type="primary"
            @click="load"
          >
            重新加载
          </el-button>
        </template>
      </el-alert>
      <el-form label-position="top">
        <div class="settings-form-heading">
          <strong>每日补助标准</strong>
          <span>单位：元/天</span>
        </div>
        <div class="settings-rate-list">
          <el-form-item
            v-for="field in rateFields"
            :key="field.id"
            :label="field.label"
          >
            <el-input
              v-model="form.subsidyRates[field.id]"
              inputmode="decimal"
              maxlength="8"
              placeholder="0.01 - 10000.00"
              class="full-width"
              :aria-label="`${field.label}每日补助标准`"
            >
              <template #prepend>
                ¥
              </template>
            </el-input>
          </el-form-item>
        </div>
        <section
          class="settings-rule-summary"
          aria-labelledby="subsidy-rule-heading"
        >
          <h2 id="subsidy-rule-heading">
            补助计算规则
          </h2>
          <div class="settings-rule-row">
            <span>计算方式</span>
            <strong>商务出差按 12:00 半天边界计算</strong>
          </div>
          <div class="settings-rule-row">
            <span>项目期限</span>
            <strong>连续 30 天以内为短期，超过 30 天为长期</strong>
          </div>
          <div class="settings-rule-row">
            <span>有效天数</span>
            <strong>市外项目自动计算；同市项目由员工按制度确认</strong>
          </div>
          <p class="settings-rule-exception">
            公司内部出差通常按设置标准计算；无锡—苏州等制度例外可选择不补助。
          </p>
        </section>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="Boolean(loadError) || saving"
          :title="loadError ? '请先重新加载系统设置' : ''"
          @click="save"
        >
          保存设置
        </el-button>
      </el-form>
    </el-card>

    <el-dialog
      v-model="keywordDialogOpen"
      :title="keywordDialogTitle"
      width="min(520px, calc(100vw - 32px))"
    >
      <el-form label-position="top">
        <el-form-item
          label="关键词"
        >
          <el-input
            v-model="keywordForm.keyword"
            maxlength="100"
            show-word-limit
            placeholder="例如：文具、通信服务费"
            @input="keywordValidationError = ''"
          />
          <p
            v-if="keywordValidationError"
            class="field-error"
            role="alert"
          >
            {{ keywordValidationError }}
          </p>
          <p
            v-else
            class="field-help"
          >
            至少 2 个字符，避免单个数字误匹配日期、金额和票据号码。
          </p>
        </el-form-item>
        <el-form-item
          v-if="editingKeywordId !== null"
          label="移动到类别"
        >
          <el-select
            v-model="keywordForm.categoryId"
            class="full-width"
          >
            <el-option
              v-for="category in configurableKeywordCategories"
              :key="category.id"
              :label="category.name"
              :value="category.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="keywordDialogOpen = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="keywordSaving"
          :disabled="keywordSaving"
          @click="saveKeyword"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>
