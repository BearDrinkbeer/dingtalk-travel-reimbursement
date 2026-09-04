<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed } from 'vue'

import { useExpenseStore } from '@/stores/expense'
import { useReimbursementDraftStore } from '@/stores/reimbursementDraft'

const props = withDefaults(defineProps<{
  previewDisabledReason?: string
}>(), {
  previewDisabledReason: '',
})

const expense = useExpenseStore()
const drafts = useReimbursementDraftStore()

const disabledReason = computed(() => {
  if (!drafts.currentDraft) return '请先创建或打开报销草稿'
  if (props.previewDisabledReason) return props.previewDisabledReason
  if (drafts.pendingMutations > 0) return '请等待草稿保存完成'
  return ''
})

async function downloadExcel(): Promise<void> {
  if (disabledReason.value) {
    ElMessage.warning(disabledReason.value)
    return
  }
  try {
    await drafts.downloadExcelPreview()
    ElMessage.success('已生成当前已保存草稿的 Excel 预览')
  } catch (error) {
    ElMessage.error(
      drafts.mutationError
      || (error instanceof Error && error.message ? error.message : 'Excel 预览生成失败，请重试'),
    )
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
        :loading="drafts.downloadingPreview"
        :disabled="Boolean(disabledReason)"
        @click="downloadExcel"
      >
        预览 Excel
      </el-button>
      <p
        v-if="disabledReason"
        class="field-error"
      >
        {{ disabledReason }}
      </p>
      <p
        v-else
        class="field-help excel-preview-help"
      >
        这里只预览已保存的内容；正式提交时服务器会重新生成最终 Excel，并直接加入钉钉 OA 附件。
      </p>
    </div>
  </el-card>
</template>

<style scoped>
.excel-preview-help {
  max-width: 520px;
  text-align: right;
}

@media (max-width: 600px) {
  .excel-preview-help {
    text-align: left;
  }
}
</style>
