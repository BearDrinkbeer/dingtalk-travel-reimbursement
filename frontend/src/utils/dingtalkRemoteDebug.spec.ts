import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { initDingH5RemoteDebug } = vi.hoisted(() => ({
  initDingH5RemoteDebug: vi.fn(),
}))

vi.mock('dingtalk-h5-remote-debug', () => ({ initDingH5RemoteDebug }))

import { initializeDingTalkRemoteDebug } from './dingtalkRemoteDebug'

describe('initializeDingTalkRemoteDebug', () => {
  beforeEach(() => {
    initDingH5RemoteDebug.mockReset()
  })

  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it('does not load remote debugging unless explicitly enabled', async () => {
    vi.stubEnv('VITE_DINGTALK_REMOTE_DEBUG', 'false')

    await expect(initializeDingTalkRemoteDebug()).resolves.toBe(false)
    expect(initDingH5RemoteDebug).not.toHaveBeenCalled()
  })

  it('initializes remote debugging in the explicit development mode', async () => {
    vi.stubEnv('VITE_DINGTALK_REMOTE_DEBUG', 'true')

    await expect(initializeDingTalkRemoteDebug()).resolves.toBe(true)
    expect(initDingH5RemoteDebug).toHaveBeenCalledOnce()
  })
})
