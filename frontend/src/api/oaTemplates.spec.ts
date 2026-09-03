import { beforeEach, describe, expect, it, vi } from 'vitest'

import { http } from '@/api/http'
import {
  confirmOaTemplateCatalog,
  getOaTemplateCatalog,
  inspectOaTemplateCatalog,
} from '@/api/oaTemplates'
import type {
  ConfirmOaTemplateCatalogInput,
  InspectOaTemplateCatalogInput,
} from '@/types/oa'

vi.mock('@/api/http', () => ({
  http: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}))

describe('OA template catalog API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loads the complete catalog state', async () => {
    const state = {
      configured: false,
      configVersion: null,
      compatibilityStatus: 'UNCONFIGURED' as const,
      isSubmissionReady: false,
      requiresConfirmation: true,
      catalog: null,
    }
    vi.mocked(http.get).mockResolvedValue({ data: { data: state } })

    await expect(getOaTemplateCatalog()).resolves.toEqual(state)
    expect(http.get).toHaveBeenCalledWith('/admin/oa/templates/catalog')
  })

  it('uses the catalog inspection endpoint with a long operation timeout', async () => {
    const input: InspectOaTemplateCatalogInput = {
      reimbursementProcessCode: 'PROC-REIMBURSEMENT',
      travelProfiles: [{
        profileKey: 'business',
        displayName: '商务出差',
        processCode: 'PROC-TRAVEL',
      }],
    }
    const inspected = { configured: false }
    vi.mocked(http.post).mockResolvedValue({ data: { data: inspected } })

    await inspectOaTemplateCatalog(input)

    expect(http.post).toHaveBeenCalledWith(
      '/admin/oa/templates/catalog/inspect',
      input,
      { timeout: 60_000 },
    )
  })

  it('saves the entire catalog in one version-checked request', async () => {
    const input: ConfirmOaTemplateCatalogInput = {
      expectedConfigVersion: 3,
      reimbursement: {
        processCode: 'PROC-REIMBURSEMENT',
        schemaFingerprint: 'a'.repeat(64),
        mappings: { company: 'company-id' },
      },
      travelProfiles: [{
        profileKey: 'business',
        displayName: '商务出差',
        processCode: 'PROC-TRAVEL',
        schemaFingerprint: 'b'.repeat(64),
        mappings: { startDate: 'start-id', endDate: 'end-id' },
        travelTypeOption: { value: 'business', label: '商务出差', key: null },
      }],
      relatedApprovalSmokeTestConfirmed: true,
    }
    vi.mocked(http.put).mockResolvedValue({ data: { data: { configVersion: 4 } } })

    await confirmOaTemplateCatalog(input)

    expect(http.put).toHaveBeenCalledWith(
      '/admin/oa/templates/catalog',
      input,
      { timeout: 60_000 },
    )
  })
})
