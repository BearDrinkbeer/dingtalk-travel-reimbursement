import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { fetchReadiness } from '@/api/health'

export const useHealthStore = defineStore('health', () => {
  const loading = ref(false)
  const available = ref<boolean | null>(null)

  const label = computed(() => {
    if (loading.value) return '正在连接服务…'
    if (available.value === true) return '服务已就绪'
    if (available.value === false) return '服务暂不可用'
    return '尚未检查服务'
  })

  async function check(): Promise<void> {
    loading.value = true
    try {
      const result = await fetchReadiness()
      available.value = result.status === 'ready'
    } catch {
      available.value = false
    } finally {
      loading.value = false
    }
  }

  return { loading, available, label, check }
})
