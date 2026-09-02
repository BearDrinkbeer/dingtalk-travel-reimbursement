import { http } from './http'
import type { ApiEnvelope } from '@/types/auth'
import type { Project, ProjectInput } from '@/types/projects'

export async function searchProjects(q = ''): Promise<Project[]> {
  const response = await http.get<ApiEnvelope<Project[]>>('/projects', { params: { q } })
  return response.data.data
}

export async function listAdminProjects(q = ''): Promise<Project[]> {
  const response = await http.get<ApiEnvelope<Project[]>>('/admin/projects', { params: { q } })
  return response.data.data
}

export async function createProject(input: ProjectInput): Promise<Project> {
  const response = await http.post<ApiEnvelope<Project>>('/admin/projects', input)
  return response.data.data
}

export async function updateProject(id: number, input: ProjectInput): Promise<Project> {
  const response = await http.put<ApiEnvelope<Project>>(`/admin/projects/${id}`, input)
  return response.data.data
}

export async function deleteProject(id: number): Promise<void> {
  await http.delete(`/admin/projects/${id}`)
}
