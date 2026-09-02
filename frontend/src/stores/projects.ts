import { defineStore } from 'pinia'
import { ref } from 'vue'

import { searchProjects } from '@/api/projects'
import { apiErrorMessage } from '@/api/errors'
import type { Project } from '@/types/projects'

export const useProjectsStore = defineStore('projects', () => {
  const options = ref<Project[]>([])
  const loading = ref(false)
  const errorMessage = ref('')

  async function search(query = ''): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
      options.value = await searchProjects(query)
    } catch (error) {
      options.value = []
      errorMessage.value = apiErrorMessage(error, '项目列表加载失败，请重试')
    } finally {
      loading.value = false
    }
  }

  return { options, loading, errorMessage, search }
})
