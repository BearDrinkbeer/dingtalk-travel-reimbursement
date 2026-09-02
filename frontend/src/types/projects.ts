export interface Project {
  id: number
  projectCode: string | null
  projectName: string
  enabled: boolean
}

export interface ProjectInput {
  projectCode: string | null
  projectName: string
  enabled: boolean
}
