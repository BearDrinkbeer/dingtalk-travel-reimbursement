<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, ref } from 'vue'

import {
  excelDownloadErrorMessage,
  generateAndDownloadExpenseExcel,
} from '@/api/excel'
import { useAuthStore } from '@/stores/auth'
import { useExpenseStore } from '@/stores/expense'

const auth = useAuthStore()
const expense = useExpenseStore()
const excelDownloading = ref(false)

const excelDisabledReason = computed(() => {
  if (!auth.session?.selectedDepartment) return '请先选择本次报销部门'
  return expense.excelDisabledReason
})

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
