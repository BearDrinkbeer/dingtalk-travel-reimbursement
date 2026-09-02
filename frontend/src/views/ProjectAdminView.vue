<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import {
  createProject,
  deleteProject,
  listAdminProjects,
  updateProject,
} from '@/api/projects'
import { apiErrorMessage } from '@/api/errors'
import type { Project, ProjectInput } from '@/types/projects'

const projects = ref<Project[]>([])
const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const query = ref('')
const dialogOpen = ref(false)
const editingId = ref<number | null>(null)
const form = reactive<ProjectInput>({ projectCode: null, projectName: '', enabled: true })

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    projects.value = await listAdminProjects(query.value)
  } catch (error) {
    projects.value = []
    loadError.value = apiErrorMessage(error, '项目列表加载失败，请重试')
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editingId.value = null
  Object.assign(form, { projectCode: null, projectName: '', enabled: true })
  dialogOpen.value = true
}

function openEdit(project: Project): void {
  editingId.value = project.id
  Object.assign(form, project)
  dialogOpen.value = true
}

async function save(): Promise<void> {
  if (!form.projectName.trim()) {
    ElMessage.warning('请填写项目名称')
    return
  }
  const input = {
    projectCode: form.projectCode?.trim() || null,
    projectName: form.projectName.trim(),
    enabled: form.enabled,
  }
  saving.value = true
  try {
    if (editingId.value === null) await createProject(input)
    else await updateProject(editingId.value, input)
    dialogOpen.value = false
    ElMessage.success('项目已保存')
    await load()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, '项目保存失败，请重试'))
  } finally {
    saving.value = false
  }
}

async function remove(project: Project): Promise<void> {
  try {
    await ElMessageBox.confirm(`确定删除“${project.projectName}”吗？`, '删除项目', {
      type: 'warning',
    })
    await deleteProject(project.id)
    ElMessage.success('项目已删除')
    await load()
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    ElMessage.error(apiErrorMessage(error, '项目删除失败，请重试'))
  }
}

onMounted(load)
</script>

<template>
  <main class="page-shell">
    <div class="admin-page-header">
      <div>
        <nav
          class="admin-links admin-page-nav"
          aria-label="管理员功能"
        >
          <RouterLink to="/">
            报销单
          </RouterLink>
          <RouterLink
            to="/admin/projects"
            aria-current="page"
          >
            项目管理
          </RouterLink>
          <RouterLink to="/admin/settings">
            系统设置
          </RouterLink>
        </nav>
        <h1>项目管理</h1>
      </div>
      <el-button
        type="primary"
        @click="openCreate"
      >
        新增项目
      </el-button>
    </div>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input
          v-model="query"
          clearable
          placeholder="搜索项目编号或名称"
          aria-label="搜索项目编号或名称"
          @keyup.enter="load"
          @clear="load"
        />
        <el-button @click="load">
          搜索
        </el-button>
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
      <el-empty
        v-else-if="!loading && projects.length === 0"
        description="暂无项目，可新增或调整搜索条件"
        :image-size="80"
      />
      <div
        v-else
        class="admin-table-scroll"
        :aria-busy="loading"
      >
        <el-table
          v-loading="loading"
          :data="projects"
        >
          <el-table-column
            prop="projectCode"
            label="项目编号"
            min-width="140"
          />
          <el-table-column
            prop="projectName"
            label="项目名称"
            min-width="220"
          />
          <el-table-column
            label="状态"
            width="90"
          >
            <template #default="scope">
              <el-tag :type="scope.row.enabled ? 'success' : 'info'">
                {{ scope.row.enabled ? '启用' : '停用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="150"
            fixed="right"
          >
            <template #default="scope">
              <el-button
                link
                type="primary"
                @click="openEdit(scope.row)"
              >
                编辑
              </el-button>
              <el-button
                link
                type="danger"
                @click="remove(scope.row)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>

    <el-dialog
      v-model="dialogOpen"
      :title="editingId === null ? '新增项目' : '编辑项目'"
      width="min(520px, calc(100vw - 32px))"
    >
      <el-form label-position="top">
        <el-form-item label="项目编号（可选）">
          <el-input
            v-model="form.projectCode"
            maxlength="64"
          />
        </el-form-item>
        <el-form-item label="项目名称">
          <el-input
            v-model="form.projectName"
            maxlength="255"
            show-word-limit
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch
            v-model="form.enabled"
            active-text="启用"
            inactive-text="停用"
          />
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
  </main>
</template>
