interface AuthCodeResult {
  code: string
}

interface DingTalkCallbacks {
  corpId: string
  clientId?: string
  success?: (result: AuthCodeResult) => void
  fail?: (error: unknown) => void
}

interface DingTalkBridge {
  ready?: (callback: () => void) => void
  error?: (callback: (error: unknown) => void) => void
  requestAuthCode?: (
    input: DingTalkCallbacks,
  ) => Promise<AuthCodeResult> | AuthCodeResult | void
}

function readableError(error: unknown): Error {
  if (error instanceof Error) return error
  return new Error('无法从钉钉获取免登授权码')
}

export async function requestDingTalkAuthCode(corpId: string, clientId: string): Promise<string> {
  // Existing sessions and development Mock do not need the large DingTalk SDK.
  // Load it only when a real免登 handshake is actually required.
  const { default: dd } = await import('dingtalk-jsapi')
  const bridge = dd as unknown as DingTalkBridge
  return new Promise((resolve, reject) => {
    let settled = false
    const succeed = (result: AuthCodeResult) => {
      if (!settled && result?.code) {
        settled = true
        resolve(result.code)
      }
    }
    const fail = (error: unknown) => {
      if (!settled) {
        settled = true
        reject(readableError(error))
      }
    }
    const invoke = () => {
      const request = bridge.requestAuthCode
      if (!request) {
        fail(new Error('当前钉钉客户端不支持 dd.requestAuthCode，请升级客户端后重试'))
        return
      }
      try {
        const result = request({ corpId, clientId, success: succeed, fail })
        if (result && typeof (result as Promise<AuthCodeResult>).then === 'function') {
          void (result as Promise<AuthCodeResult>).then(succeed, fail)
        } else if (result && 'code' in result) {
          succeed(result)
        }
      } catch (error) {
        fail(error)
      }
    }

    bridge.error?.(fail)
    if (typeof bridge.ready === 'function') bridge.ready(invoke)
    else invoke()
  })
}
