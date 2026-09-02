import { describe, expect, it } from 'vitest'

import {
  DEFAULT_RECEIPT_LIMITS,
  validateReceiptFiles,
} from '@/utils/receiptFiles'

const {
  maxFiles: MAX_RECEIPT_FILES,
  maxFileBytes: MAX_RECEIPT_FILE_BYTES,
  maxSessionBytes: MAX_RECEIPT_SESSION_BYTES,
} = DEFAULT_RECEIPT_LIMITS

function fixture(name: string, size: number, type = ''): File {
  return { name, size, type } as File
}

describe('receipt file validation', () => {
  it('accepts supported types and rejects type mismatches or unsupported extensions independently', () => {
    const result = validateReceiptFiles([
      fixture('a.jpg', 100, 'image/jpeg'),
      fixture('b.JPEG', 100),
      fixture('c.png', 100, 'application/pdf'),
      fixture('d.heic', 100, 'image/heic'),
      fixture('e.pdf', 100, 'application/pdf'),
    ])

    expect(result.accepted.map((file) => file.name)).toEqual(['a.jpg', 'b.JPEG', 'e.pdf'])
    expect(result.rejected).toHaveLength(2)
  })

  it('uses integer byte limits for per-file and retained Session checks', () => {
    const countResult = validateReceiptFiles([
      fixture('empty.png', 0, 'image/png'),
      fixture('large.png', MAX_RECEIPT_FILE_BYTES + 1, 'image/png'),
      fixture('one-slot.pdf', MAX_RECEIPT_FILE_BYTES, 'application/pdf'),
      fixture('no-slot.png', 1, 'image/png'),
    ], MAX_RECEIPT_FILES - 1)

    expect(countResult.accepted.map((file) => file.name)).toEqual(['one-slot.pdf'])
    expect(countResult.rejected.map((entry) => entry.message)).toEqual([
      '文件为空或大小无效',
      '单个文件不能超过 20 MiB',
      `当前最多保留 ${MAX_RECEIPT_FILES} 个待处理票据文件`,
    ])

    const sessionResult = validateReceiptFiles(
      [fixture('fits.pdf', 1, 'application/pdf'), fixture('overflow.pdf', 1, 'application/pdf')],
      1,
      MAX_RECEIPT_SESSION_BYTES - 1,
    )
    expect(sessionResult.accepted.map((file) => file.name)).toEqual(['fits.pdf'])
    expect(sessionResult.rejected[0]?.message).toBe('当前会话保留的票据总大小不能超过 100 MiB')
  })

  it('uses limits supplied by the backend public configuration', () => {
    const limits = { maxFiles: 1, maxFileBytes: 10, maxSessionBytes: 10 }
    const result = validateReceiptFiles(
      [fixture('a.png', 10, 'image/png'), fixture('b.png', 1, 'image/png')],
      0,
      0,
      limits,
    )

    expect(result.accepted.map((file) => file.name)).toEqual(['a.png'])
    expect(result.rejected[0]?.message).toBe('当前最多保留 1 个待处理票据文件')
  })
})
