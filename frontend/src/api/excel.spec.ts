import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  downloadBlob,
  filenameFromContentDisposition,
  generateExpenseExcel,
} from '@/api/excel'
import { http } from '@/api/http'
import type { ExcelGeneratePayload } from '@/types/expenses'

const payload: ExcelGeneratePayload = {
  project: { mode: 'manual', text: '示例项目' },
  trip: {
    tripType: 'business',
    startDate: '2026-06-30',
    startTime: '09:00',
    endDate: '2026-07-07',
    endTime: '18:00',
  },
  items: [],
}

describe('Excel download API', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('prefers the RFC 5987 filename and removes path components', () => {
    const encoded = encodeURIComponent('目录/差旅费报销单-测试.xlsx')
    expect(
      filenameFromContentDisposition(
        `attachment; filename=expense-report.xlsx; filename*=UTF-8''${encoded}`,
      ),
    ).toBe('差旅费报销单-测试.xlsx')
    expect(filenameFromContentDisposition('attachment; filename="unsafe.txt"')).toBe(
      '差旅费报销单.xlsx',
    )
  })

  it('requests a blob and returns the server filename', async () => {
    const blob = new Blob(['xlsx'], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    const post = vi.spyOn(http, 'post').mockResolvedValue({
      data: blob,
      headers: {
        'content-disposition':
          `attachment; filename=expense-report.xlsx; filename*=UTF-8''${encodeURIComponent('差旅费报销单-示例.xlsx')}`,
      },
    })

    await expect(generateExpenseExcel(payload)).resolves.toEqual({
      blob,
      filename: '差旅费报销单-示例.xlsx',
    })
    expect(post).toHaveBeenCalledWith('/excel/generate', payload, {
      responseType: 'blob',
      timeout: 30_000,
    })
  })

  it('downloads through a temporary object URL and always revokes it', () => {
    const createObjectURL = vi.fn(() => 'blob:test')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const blob = new Blob(['xlsx'])

    downloadBlob(blob, '差旅费报销单.xlsx')

    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:test')
    expect(document.querySelector('a[download="差旅费报销单.xlsx"]')).toBeNull()
  })
})
