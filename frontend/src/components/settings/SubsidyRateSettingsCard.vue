<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { apiErrorMessage } from '@/api/errors'
import {
  getExpenseSettings,
  normalizeSubsidyRate,
  updateExpenseSettings,
} from '@/api/settings'
import type { SubsidyRateType } from '@/types/expenses'

const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
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

onMounted(load)

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
</script>

<template>
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
</template>
