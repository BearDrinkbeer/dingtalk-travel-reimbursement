import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { fetchReadiness } from '@/api/health'
import { useHealthStore } from './health'

vi.mock('@/api/health', () => ({
  fetchReadiness: vi.fn(),
}))

describe('health store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(fetchReadiness).mockReset()
  })

  it('marks the backend available after a successful check', async () => {
    vi.mocked(fetchReadiness).mockResolvedValue({
      status: 'ready',
      checks: {
        database: 'ok',
        excelTemplate: 'ok',
        tempStorage: 'ok',
        ocr: 'disabled',
      },
    })
    const store = useHealthStore()

    await store.check()

    expect(store.available).toBe(true)
    expect(store.label).toBe('服务已就绪')
  })

  it('keeps the placeholder usable when the backend is unavailable', async () => {
    vi.mocked(fetchReadiness).mockRejectedValue(new Error('offline'))
    const store = useHealthStore()

    await store.check()

    expect(store.available).toBe(false)
    expect(store.label).toBe('服务暂不可用')
  })
})
