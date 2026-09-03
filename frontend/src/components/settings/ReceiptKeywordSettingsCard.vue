<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import { apiErrorMessage } from '@/api/errors'
import { getExpenseCategories } from '@/api/expenses'
import {
  createReceiptKeyword,
  deleteReceiptKeyword,
  listReceiptKeywords,
  updateReceiptKeyword,
} from '@/api/receiptKeywords'
import type { ReceiptKeywordMapping } from '@/api/receiptKeywords'
import type { ExpenseCategoryMetadata } from '@/types/expenses'

const keywordMappings = ref<ReceiptKeywordMapping[]>([])
const keywordCategories = ref<ExpenseCategoryMetadata[]>([])
const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const dialogOpen = ref(false)
const editingKeywordId = ref<number | null>(null)
const validationError = ref('')
const form = reactive({ keyword: '', categoryId: '' })

const configurableCategories = computed(() =>
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
const dialogTitle = computed(() => {
  if (editingKeywordId.value !== null) return '编辑分类关键词'
  const category = keywordCategories.value.find(
    (item) => item.id === form.categoryId,
  )
  return category ? `为${category.name}添加关键词` : '新增分类关键词'
})

onMounted(load)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
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
    loadError.value = apiErrorMessage(error, '分类关键词加载失败，请重试')
  } finally {
    loading.value = false
  }
}

function openCreate(categoryId: string): void {
  editingKeywordId.value = null
  validationError.value = ''
  Object.assign(form, { keyword: '', categoryId })
  dialogOpen.value = true
}

function openEdit(mapping: ReceiptKeywordMapping): void {
  editingKeywordId.value = mapping.id
  validationError.value = ''
  Object.assign(form, {
    keyword: mapping.keyword,
    categoryId: mapping.categoryId,
  })
  dialogOpen.value = true
}

async function save(): Promise<void> {
  const keyword = form.keyword.trim().replace(/\s+/g, ' ')
  if (keyword.length < 2) {
    validationError.value = /^\d$/.test(keyword)
      ? '单个数字容易误匹配日期、金额和票据号码，请填写至少 2 个字符'
      : '关键词至少填写 2 个字符'
    return
  }
  validationError.value = ''
  if (!configurableCategories.value.some((category) => category.id === form.categoryId)) {
    ElMessage.warning('请选择费用类别')
    return
  }
  saving.value = true
  try {
    const input = { keyword, categoryId: form.categoryId }
    if (editingKeywordId.value === null) await createReceiptKeyword(input)
    else await updateReceiptKeyword(editingKeywordId.value, input)
    dialogOpen.value = false
    ElMessage.success('分类关键词已保存')
    await load()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '分类关键词保存失败，请重试'))
  } finally {
    saving.value = false
  }
}

async function remove(mapping: ReceiptKeywordMapping): Promise<void> {
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
    await load()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '分类关键词删除失败，请重试'))
  }
}
</script>

<template>
  <el-card
    v-loading="loading"
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
              @click="openEdit(mapping)"
              @close.stop="remove(mapping)"
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
          @click="openCreate(group.category.id)"
        >
          添加
        </el-button>
      </section>
    </div>
  </el-card>

  <el-dialog
    v-model="dialogOpen"
    :title="dialogTitle"
    width="min(520px, calc(100vw - 32px))"
  >
    <el-form label-position="top">
      <el-form-item label="关键词">
        <el-input
          v-model="form.keyword"
          maxlength="100"
          show-word-limit
          placeholder="例如：文具、通信服务费"
          @input="validationError = ''"
        />
        <p
          v-if="validationError"
          class="field-error"
          role="alert"
        >
          {{ validationError }}
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
          v-model="form.categoryId"
          class="full-width"
        >
          <el-option
            v-for="category in configurableCategories"
            :key="category.id"
            :label="category.name"
            :value="category.id"
          />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogOpen = false">
        取消
      </el-button>
      <el-button
        type="primary"
        :loading="saving"
        :disabled="saving"
        @click="save"
      >
        保存
      </el-button>
    </template>
  </el-dialog>
</template>
