export async function initializeDingTalkRemoteDebug(): Promise<boolean> {
  if (!import.meta.env.DEV || import.meta.env.VITE_DINGTALK_REMOTE_DEBUG !== 'true') {
    return false
  }

  const { initDingH5RemoteDebug } = await import('dingtalk-h5-remote-debug')
  initDingH5RemoteDebug()
  return true
}
