import axios from 'axios'

let csrfToken: string | null = null
let unauthorizedHandler: (() => void) | null = null

export const http = axios.create({
  baseURL: '/api',
  timeout: 10_000,
  withCredentials: true,
  headers: {
    Accept: 'application/json',
  },
})

export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

http.interceptors.request.use((config) => {
  const method = config.method?.toUpperCase()
  if (csrfToken && method && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    config.headers.set('X-CSRF-Token', csrfToken)
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      csrfToken = null
      unauthorizedHandler?.()
    }
    return Promise.reject(error)
  },
)
