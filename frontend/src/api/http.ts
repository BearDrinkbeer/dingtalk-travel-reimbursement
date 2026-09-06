import axios from 'axios'

let csrfToken: string | null = null
let authGeneration = 0
const requestAuthGenerations = new WeakMap<object, number>()
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
  authGeneration += 1
}

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

http.interceptors.request.use((config) => {
  requestAuthGenerations.set(config, authGeneration)
  const method = config.method?.toUpperCase()
  if (csrfToken && method && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    config.headers.set('X-CSRF-Token', csrfToken)
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401
      && error.config && requestAuthGenerations.get(error.config) === authGeneration) {
      setCsrfToken(null)
      unauthorizedHandler?.()
    }
    return Promise.reject(error)
  },
)
