import { http } from './http'

export interface SuccessResponse<T> {
  success: true
  data: T
}

export interface HealthData {
  status: 'ok'
}

export interface ReadinessData {
  status: 'ready'
  checks: {
    database: 'ok'
    excelTemplate: 'ok'
    tempStorage: 'ok'
    ocr: 'disabled' | 'development_fake' | 'configured'
  }
}

export async function fetchHealth(): Promise<HealthData> {
  const response = await http.get<SuccessResponse<HealthData>>('/health')
  return response.data.data
}

export async function fetchReadiness(): Promise<ReadinessData> {
  const response = await http.get<SuccessResponse<ReadinessData>>('/ready')
  return response.data.data
}
