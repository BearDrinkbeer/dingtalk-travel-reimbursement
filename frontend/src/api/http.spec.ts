import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { http, setCsrfToken, setUnauthorizedHandler } from '@/api/http'

describe('HTTP session expiry handling', () => {
  afterEach(() => {
    setCsrfToken(null)
    setUnauthorizedHandler(null)
  })

  it.each([null, 'previous-session'])(
    'ignores a late calculation 401 from before the current login (previous token: %s)', async (previousToken) => {
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    setCsrfToken(previousToken)
    let finish!: () => void
    let started!: () => void
    const requestStarted = new Promise<void>((resolve) => { started = resolve })
    const request = http.post('/calculate/totals', {}, {
      adapter: (config: InternalAxiosRequestConfig) => new Promise((_resolve, reject) => {
        finish = () => reject(new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, {
          data: {}, status: 401, statusText: 'Unauthorized', headers: {}, config,
        }))
        started()
      }),
    }).catch((error: unknown) => error)
    await requestStarted
    setCsrfToken('new-authenticated-session')
    finish()
    expect(await request).toBeInstanceOf(AxiosError)
    expect(unauthorized).not.toHaveBeenCalled()

    await expect(http.get('/me', {
      adapter: async (config) => {
        throw new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, {
          data: {}, status: 401, statusText: 'Unauthorized', headers: {}, config,
        })
      },
    })).rejects.toBeInstanceOf(AxiosError)
    expect(unauthorized).toHaveBeenCalledOnce()
    },
  )
})
