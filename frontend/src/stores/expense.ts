import axios from 'axios'
import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'

import { apiErrorMessage } from '@/api/errors'
import { calculateTotals, getExpenseCategories } from '@/api/expenses'
import {
  deleteReceiptFile,
  recognizeReceiptFile,
  uploadReceiptFile,
} from '@/api/receipts'
import type {
  ExpenseCategoryMetadata,
  ExcelGeneratePayload,
  ExcelProjectInput,
  ExpenseItem,
  ExpenseTotals,
  TripInput,
  TripType,
} from '@/types/expenses'
import type { OcrReceiptCandidate, ReceiptFileState } from '@/types/receipts'
import type { ReceiptUploadLimits } from '@/types/auth'
import { centsToMoney, moneyToCents } from '@/utils/money'
import { DEFAULT_RECEIPT_LIMITS, validateReceiptFiles } from '@/utils/receiptFiles'

const SPECIAL_TRIP_TYPES = new Set<TripType>([
  'same_city_project',
  'internal',
])
const MINUTE_TIME_PATTERN = /^(?:[01]\d|2[0-3]):[0-5]\d$/
const EFFECTIVE_DAYS_PATTERN = /^(?:0|[1-9]\d{0,2})(?:\.(?:0|5))?$/
const MAX_CONFIRMED_DAYS = 366
const CALENDAR_DATE_PATTERN = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/
function normalizeEffectiveDays(value: string): string | null {
  const normalized = value.trim()
  if (!EFFECTIVE_DAYS_PATTERN.test(normalized)) return null
  const parsed = Number(normalized)
  if (!Number.isFinite(parsed) || parsed > MAX_CONFIRMED_DAYS) return null
  return parsed.toFixed(1)
}

function newItemId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function newReceiptId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `receipt-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function isCalendarDate(value: string | null): value is string {
  if (!value || !CALENDAR_DATE_PATTERN.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  const parsed = new Date(Date.UTC(year!, month! - 1, day!))
  return parsed.getUTCFullYear() === year
    && parsed.getUTCMonth() === month! - 1
    && parsed.getUTCDate() === day
}

function calendarDays(startDate: string, endDate: string): number | null {
  if (!isCalendarDate(startDate) || !isCalendarDate(endDate)) return null
  const start = Date.parse(`${startDate}T00:00:00Z`)
  const end = Date.parse(`${endDate}T00:00:00Z`)
  const days = Math.floor((end - start) / 86_400_000) + 1
  return days > 0 ? days : null
}

export const useExpenseStore = defineStore('expense', () => {
  const manualProject = ref(false)
  const selectedProjectId = ref<number | null>(null)
  const manualProjectText = ref('')
  const trip = reactive({
    tripType: 'business' as TripType,
    startDate: '',
    startTime: '09:00',
    endDate: '',
    endTime: '18:00',
    policyConfirmed: false,
    confirmedEffectiveDays: '',
    noSubsidyException: false,
  })
  const items = ref<ExpenseItem[]>([])
  const includeSubsidy = ref(false)
  const receiptFiles = ref<ReceiptFileState[]>([])
  const receiptUploadLimits = ref<ReceiptUploadLimits>({ ...DEFAULT_RECEIPT_LIMITS })
  const ocrUnavailable = ref(false)
  const categories = ref<ExpenseCategoryMetadata[]>([])
  const categoriesLoading = ref(false)
  const categoryLoadError = ref('')
  const totals = ref<ExpenseTotals | null>(null)
  const calculating = ref(false)
  const calculationError = ref('')
  let calculationVersion = 0
  const calculatedSignature = ref('')
  let receiptOperationVersion = 0
  const receiptControllers = new Set<AbortController>()

  const projectCalendarDays = computed(() =>
    trip.tripType === 'project' ? calendarDays(trip.startDate, trip.endDate) : null,
  )
  const projectPolicyType = computed(() => {
    if (trip.tripType !== 'project' || projectCalendarDays.value === null) return null
    return projectCalendarDays.value > 30 ? 'long_term_project' : 'short_term_project'
  })
  const requiresPolicyConfirmation = computed(
    () => includeSubsidy.value && SPECIAL_TRIP_TYPES.has(trip.tripType),
  )
  const maxExpenseItems = ref(200)
  const policyInputError = computed(() => {
    if (!includeSubsidy.value) return ''
    if (!requiresPolicyConfirmation.value) return ''
    const effectiveDays = normalizeEffectiveDays(trip.confirmedEffectiveDays)
    if (effectiveDays === null) return '有效天数必须在 0 到 366 天之间，并以 0.5 天为单位'
    if (!trip.policyConfirmed) return '请勾选确认本次有效天数'
    return ''
  })
  const manualCategories = computed(() => categories.value.filter((item) => item.manualSelectable))
  const sortedItems = computed(() =>
    items.value
      .map((item, index) => ({ item, index }))
      .sort((left, right) => {
        const leftDate = isCalendarDate(left.item.date ?? null) ? left.item.date! : null
        const rightDate = isCalendarDate(right.item.date ?? null) ? right.item.date! : null
        if (leftDate && rightDate) {
          const dateOrder = leftDate.localeCompare(rightDate)
          return dateOrder || left.index - right.index
        }
        if (leftDate) return -1
        if (rightDate) return 1
        return left.index - right.index
      })
      .map(({ item }) => item),
  )
  const receiptBusy = computed(() =>
    receiptFiles.value.some((file) => ['uploading', 'recognizing'].includes(file.status)),
  )
  const localExpenseCents = computed(() =>
    items.value.reduce((sum, item) => sum + (moneyToCents(item.amount) ?? 0), 0),
  )
  const localReceiptCount = computed(() =>
    items.value.reduce((sum, item) => sum + item.receiptCount, 0),
  )
  const displayExpenseTotal = computed(
    () => totals.value?.expenseTotal ?? centsToMoney(localExpenseCents.value),
  )
  const displaySubsidyTotal = computed(() => totals.value?.subsidyTotal ?? '0.00')
  const displayReceiptCount = computed(() => totals.value?.receiptCount ?? localReceiptCount.value)
  const displayTotal = computed(() => {
    if (totals.value) return totals.value.totalAmount
    const subsidyCents = moneyToCents(displaySubsidyTotal.value) ?? 0
    return centsToMoney(localExpenseCents.value + subsidyCents)
  })
  const itemReadinessError = computed(() => {
    if (items.value.some((item) => !item.date || !CALENDAR_DATE_PATTERN.test(item.date))) {
      return '请补全每条费用明细的发生日期'
    }
    if (items.value.some((item) => moneyToCents(item.amount) === null)) {
      return '请补全每条费用明细的金额，最多两位小数'
    }
    if (items.value.some((item) => !item.displayDate.trim() || !item.description.trim())) {
      return '请补全每条费用明细的日期说明和用途说明'
    }
    return ''
  })

  function calculationSignature(payload: TripInput | null): string {
    return JSON.stringify({
      trip: payload,
      items: items.value.map((item) => ({
        category: item.category,
        date: item.date,
        displayDate: item.displayDate,
        description: item.description,
        amount: item.amount,
        receiptCount: item.receiptCount,
      })),
    })
  }

  function tripPayload(): TripInput | null {
    if (!includeSubsidy.value) return null
    if (
      !trip.startDate ||
      !trip.endDate ||
      !MINUTE_TIME_PATTERN.test(trip.startTime) ||
      !MINUTE_TIME_PATTERN.test(trip.endTime)
    ) return null
    const payload: TripInput = {
      tripType: trip.tripType,
      startDate: trip.startDate,
      startTime: trip.startTime,
      endDate: trip.endDate,
      endTime: trip.endTime,
    }
    if (requiresPolicyConfirmation.value) {
      if (policyInputError.value) return null
      const effectiveDays = normalizeEffectiveDays(trip.confirmedEffectiveDays)
      if (effectiveDays === null) return null
      payload.policyConfirmed = true
      payload.confirmedEffectiveDays = effectiveDays
      if (trip.tripType === 'internal' && trip.noSubsidyException) {
        payload.noSubsidyException = true
      }
    }
    return payload
  }

  async function loadCategories(force = false): Promise<boolean> {
    if (categoriesLoading.value) return categories.value.length > 0
    if (categories.value.length && !force) return true
    categoriesLoading.value = true
    categoryLoadError.value = ''
    try {
      const loaded = await getExpenseCategories()
      if (!loaded.some((item) => item.manualSelectable)) {
        throw new Error('no manually selectable expense category')
      }
      categories.value = loaded
      return true
    } catch {
      categories.value = []
      categoryLoadError.value = '费用类别加载失败，请重试后再添加费用明细'
      return false
    } finally {
      categoriesLoading.value = false
    }
  }

  function upsertManualItem(input: Omit<ExpenseItem, 'id' | 'source'> & { id?: string }): string {
    if (!manualCategories.value.some((category) => category.id === input.category)) {
      throw new Error('费用类别不可用，请重新加载类别后选择')
    }
    if (items.value.length >= maxExpenseItems.value && !input.id) {
      throw new Error(`当前最多添加 ${maxExpenseItems.value} 条票据费用明细`)
    }
    const amountCents = moneyToCents(input.amount)
    if (amountCents === null) throw new Error('金额必须为非负数，且最多两位小数')
    if (!Number.isInteger(input.receiptCount) || input.receiptCount < 1) {
      throw new Error('票据张数至少为 1')
    }
    const id = input.id ?? newItemId()
    const existing = items.value.find((item) => item.id === id)
    const source = existing?.source === 'ocr' ? 'ocr' : 'manual'
    const item: ExpenseItem = {
      ...input,
      id,
      source,
      amount: centsToMoney(amountCents),
      displayDate: input.displayDate.trim(),
      description: input.description.trim(),
      confidence: existing?.confidence,
      warnings: input.warnings,
    }
    if (!item.displayDate || !item.description) throw new Error('日期和说明不能为空')
    const index = items.value.findIndex((existing) => existing.id === id)
    if (index >= 0) items.value[index] = item
    else items.value.push(item)
    totals.value = null
    calculatedSignature.value = ''
    return id
  }

  function stageOcrCandidate(fileId: string, candidate: OcrReceiptCandidate): boolean {
    const receipt = receiptFiles.value.find((entry) => entry.tempId === fileId)
    if (!receipt) return false
    const candidateCategory = manualCategories.value.find(
      (category) => category.id === candidate.categoryId,
    )
    const category = candidateCategory
      ?? manualCategories.value.find((entry) => entry.id === 'other')
      ?? manualCategories.value[0]
    if (!category) {
      receipt.status = 'failed'
      receipt.error = '识别结果的费用类别不可用，请重新加载类别后手工添加'
      receipt.candidate = undefined
      return false
    }
    const confidenceNumber = Number(candidate.confidence)
    const confidence = Number.isFinite(confidenceNumber)
      ? Math.min(1, Math.max(0, confidenceNumber)).toFixed(2)
      : '0.00'
    const warnings = new Set(candidate.warnings.filter((warning) => warning.trim()))
    if (!candidateCategory) warnings.add('MANUAL_REVIEW_REQUIRED')
    if (!isCalendarDate(candidate.date)) warnings.add('MISSING_DATE')
    const amountCents = candidate.amount === null ? null : moneyToCents(candidate.amount)
    if (amountCents === null) {
      warnings.add('MISSING_AMOUNT')
    }
    if (!candidate.description?.trim()) warnings.add('MISSING_DESCRIPTION')
    if (warnings.size) warnings.add('MANUAL_REVIEW_REQUIRED')
    receipt.candidate = {
      ...candidate,
      categoryId: category.id,
      categoryName: category.name,
      date: candidate.date ?? '',
      description: candidate.description ?? '',
      amount: candidate.amount ?? '',
      confidence,
      warnings: [...warnings],
    }
    const id = receipt.ocrItemId ?? `ocr-${receipt.tempId}`
    const item: ExpenseItem = {
      id,
      category: category.id,
      date: isCalendarDate(candidate.date) ? candidate.date : undefined,
      displayDate: isCalendarDate(candidate.date) ? candidate.date : '',
      description: candidate.description?.trim()
        || (candidate.status === 'failed' ? receipt.name : category.name),
      amount: amountCents === null ? '' : centsToMoney(amountCents),
      receiptCount: 1,
      source: 'ocr',
      confidence,
      warnings: [...warnings],
    }
    const index = items.value.findIndex((entry) => entry.id === id)
    if (index >= 0) items.value[index] = item
    else items.value.push(item)
    receipt.ocrItemId = id
    receipt.status = 'done'
    receipt.error = candidate.status === 'failed'
      ? candidate.error?.message || '票据识别失败，请编辑该条费用'
      : undefined
    totals.value = null
    calculatedSignature.value = ''
    return true
  }

  function removeItem(id: string): void {
    items.value = items.value.filter((item) => item.id !== id)
    for (const receipt of receiptFiles.value) {
      if (receipt.ocrItemId === id) {
        receipt.ocrItemId = undefined
        receipt.candidate = undefined
        receipt.status = receipt.tempId ? 'uploaded' : 'failed'
      }
    }
    totals.value = null
    calculatedSignature.value = ''
  }

  async function refreshCalculations(): Promise<void> {
    const payload = tripPayload()
    const version = ++calculationVersion
    totals.value = null
    calculatedSignature.value = ''
    calculationError.value = includeSubsidy.value ? policyInputError.value : ''
    if (includeSubsidy.value && !payload) return
    if (itemReadinessError.value) {
      calculationError.value = itemReadinessError.value
      return
    }
    calculating.value = true
    try {
      const result = await calculateTotals(payload, items.value)
      if (version === calculationVersion) {
        totals.value = result
        calculatedSignature.value = calculationSignature(payload)
      }
    } catch (error) {
      if (version !== calculationVersion) return
      calculationError.value = axios.isAxiosError(error)
        ? (error.response?.data as { error?: { message?: string } } | undefined)?.error?.message ??
          '金额计算失败，请检查填写内容'
        : '金额计算失败，请检查填写内容'
    } finally {
      if (version === calculationVersion) calculating.value = false
    }
  }

  const calculationsCurrent = computed(() => {
    const payload = tripPayload()
    if (includeSubsidy.value && !payload) return false
    return Boolean(
      totals.value && calculatedSignature.value === calculationSignature(payload),
    )
  })

  function projectPayload(): ExcelProjectInput | null {
    if (manualProject.value) {
      const text = manualProjectText.value.trim()
      return text ? { mode: 'manual', text } : null
    }
    return selectedProjectId.value && selectedProjectId.value > 0
      ? { mode: 'selected', id: selectedProjectId.value }
      : null
  }

  const excelDisabledReason = computed(() => {
    if (!projectPayload()) return manualProject.value ? '请填写报销项目/预算代码' : '请选择报销项目'
    if (items.value.length > maxExpenseItems.value) {
      return `报销单最多填写 ${maxExpenseItems.value} 条票据费用明细`
    }
    if (categoryLoadError.value || categories.value.length === 0) return '费用类别尚未正确加载'
    if (!items.value.every((item) => manualCategories.value.some((entry) => entry.id === item.category))) {
      return '费用明细中存在不可用类别'
    }
    if (itemReadinessError.value) return itemReadinessError.value
    if (includeSubsidy.value && !tripPayload()) {
      return policyInputError.value || '请完整填写出发和返回日期、时间'
    }
    if (calculating.value) return '正在重新计算，请稍候'
    if (calculationError.value) return calculationError.value
    if (!calculationsCurrent.value) return '请等待服务端完成金额计算'
    return ''
  })

  function buildExcelPayload(): ExcelGeneratePayload | null {
    const project = projectPayload()
    const tripValue = tripPayload()
    if (excelDisabledReason.value || !project) return null
    return {
      project,
      trip: tripValue,
      items: items.value.map((item) => ({
        category: item.category,
        date: item.date ?? '',
        displayDate: item.displayDate,
        description: item.description,
        amount: item.amount,
        receiptCount: item.receiptCount,
      })),
    }
  }

  function setTripType(value: TripType): void {
    trip.tripType = value
    trip.policyConfirmed = false
    trip.confirmedEffectiveDays = ''
    trip.noSubsidyException = false
    totals.value = null
    calculatedSignature.value = ''
  }

  function setSubsidyIncluded(value: boolean): void {
    includeSubsidy.value = value
    totals.value = null
    calculatedSignature.value = ''
    calculationError.value = ''
  }

  function receiptByLocalId(localId: string): ReceiptFileState | undefined {
    return receiptFiles.value.find((file) => file.localId === localId)
  }

  function receiptByItemId(itemId: string): ReceiptFileState | undefined {
    return receiptFiles.value.find((file) => file.ocrItemId === itemId)
  }

  function stageFailedReceipt(receipt: ReceiptFileState, code: string, message: string): void {
    const category = manualCategories.value.find((entry) => entry.id === 'other')
      ?? manualCategories.value[0]
    if (!category || !receipt.tempId) {
      receipt.status = 'failed'
      receipt.error = message
      return
    }
    stageOcrCandidate(receipt.tempId, {
      fileId: receipt.tempId,
      type: 'other',
      categoryId: category.id,
      categoryName: category.name,
      date: null,
      description: null,
      amount: null,
      receiptCount: 1,
      source: 'ocr',
      confidence: '0.00',
      warnings: ['MANUAL_REVIEW_REQUIRED'],
      status: 'failed',
      error: { code, message },
    })
  }

  function currentReceiptOperation(version: number): boolean {
    return version === receiptOperationVersion
  }

  async function attemptUpload(
    localId: string,
    controller: AbortController,
    version: number,
  ): Promise<boolean> {
    const receipt = receiptByLocalId(localId)
    if (!receipt || receipt.tempId) return false
    receipt.status = 'uploading'
    receipt.error = undefined
    receipt.uploadProgress = 0
    const uploaded = await uploadReceiptFile(receipt.file, {
      signal: controller.signal,
      onProgress: (percent) => {
        if (!currentReceiptOperation(version)) return
        receipt.uploadProgress = percent
      },
    })
    if (!currentReceiptOperation(version)) {
      try {
        await deleteReceiptFile(uploaded.id)
      } catch {
        // The server TTL remains the fallback if reset/logout raced the response.
      }
      return false
    }
    receipt.tempId = uploaded.id
    receipt.name = uploaded.name || receipt.name
    receipt.status = 'uploaded'
    receipt.uploadProgress = 100
    receipt.error = undefined
    return true
  }

  async function recognizeUploadedReceipts(
    localIds: string[],
    controller: AbortController,
    version: number,
  ): Promise<void> {
    const recognizing = localIds
      .map(receiptByLocalId)
      .filter((file): file is ReceiptFileState => Boolean(file?.tempId))
    if (!recognizing.length) return
    for (const receipt of recognizing) {
      receipt.status = 'recognizing'
      receipt.error = undefined
      receipt.candidate = undefined
    }
    const tripYear = includeSubsidy.value && /^\d{4}-/.test(trip.startDate)
      ? Number(trip.startDate.slice(0, 4))
      : undefined
    for (const receipt of recognizing) {
      if (!currentReceiptOperation(version)) return
      let candidate: OcrReceiptCandidate | undefined
      try {
        const candidates = await recognizeReceiptFile(receipt.tempId as string, {
          tripYear,
          signal: controller.signal,
        })
        candidate = candidates.find((item) => item.fileId === receipt.tempId)
      } catch (error) {
        if (!currentReceiptOperation(version) || axios.isCancel(error)) return
        stageFailedReceipt(receipt, 'OCR_FAILED', apiErrorMessage(
          error,
          '本地票据识别暂不可用，请编辑该条费用',
        ))
        continue
      }
      if (!candidate) {
        stageFailedReceipt(receipt, 'OCR_FAILED', '识别结果缺少该文件，请重试或编辑该条费用')
        continue
      }
      if (candidate.status === 'failed') {
        if (candidate.error?.code === 'OCR_DISABLED') ocrUnavailable.value = true
        stageOcrCandidate(candidate.fileId, candidate)
        continue
      }
      stageOcrCandidate(candidate.fileId, candidate)
    }
  }

  async function processReceiptFiles(localIds: string[]): Promise<void> {
    if (!localIds.length) return
    const version = receiptOperationVersion
    const controller = new AbortController()
    receiptControllers.add(controller)
    try {
      for (const localId of localIds) {
        if (!currentReceiptOperation(version)) return
        const receipt = receiptByLocalId(localId)
        if (!receipt) continue
        try {
          const uploaded = await attemptUpload(localId, controller, version)
          if (uploaded && currentReceiptOperation(version)) {
            await recognizeUploadedReceipts([localId], controller, version)
          }
        } catch (error) {
          if (!currentReceiptOperation(version) || axios.isCancel(error)) return
          receipt.status = 'failed'
          receipt.error = apiErrorMessage(
            error,
            '该文件上传失败，请检查格式或手工添加',
          )
          receipt.uploadProgress = 0
        }
      }
    } finally {
      receiptControllers.delete(controller)
    }
  }

  async function addReceiptFiles(
    files: readonly File[],
  ): Promise<ReturnType<typeof validateReceiptFiles>> {
    // The server quota counts retained uploads, not local rows whose upload failed.
    const activeFiles = receiptFiles.value.filter((file) => Boolean(file.tempId))
    const activeFileCount = activeFiles.length
    const activeFileBytes = activeFiles.reduce((total, file) => total + file.size, 0)
    const validation = validateReceiptFiles(
      files,
      activeFileCount,
      activeFileBytes,
      receiptUploadLimits.value,
    )
    if (receiptBusy.value) {
      return {
        accepted: [],
        rejected: files.map((file) => ({ file, message: '已有票据正在处理，请稍后再选' })),
      }
    }
    const localIds = validation.accepted.map((file) => {
      const localId = newReceiptId()
      receiptFiles.value.push({
        localId,
        file,
        name: file.name,
        size: file.size,
        uploadProgress: 0,
        status: 'queued',
      })
      return localId
    })
    await processReceiptFiles(localIds)
    return validation
  }

  function setReceiptUploadLimits(limits: ReceiptUploadLimits): void {
    receiptUploadLimits.value = { ...limits }
  }

  function setExpenseItemLimit(maxItems: number): void {
    if (Number.isSafeInteger(maxItems) && maxItems > 0) {
      maxExpenseItems.value = maxItems
    }
  }

  async function retryReceipt(localId: string): Promise<void> {
    const receipt = receiptByLocalId(localId)
    if (!receipt || receiptBusy.value) return
    if (!receipt.tempId) {
      await processReceiptFiles([localId])
      return
    }
    const version = receiptOperationVersion
    const controller = new AbortController()
    receiptControllers.add(controller)
    try {
      await recognizeUploadedReceipts([localId], controller, version)
    } finally {
      receiptControllers.delete(controller)
    }
  }

  async function removeReceipt(localId: string): Promise<boolean> {
    const receipt = receiptByLocalId(localId)
    if (!receipt || ['uploading', 'recognizing'].includes(receipt.status)) return false

    const tempId = receipt.tempId
    const linkedItemId = receipt.ocrItemId
    receiptFiles.value = receiptFiles.value.filter((entry) => entry.localId !== localId)
    if (linkedItemId) removeItem(linkedItemId)

    if (tempId) {
      try {
        await deleteReceiptFile(tempId)
      } catch {
        // The row is already removed from this reimbursement. Session logout
        // and the server TTL remain the cleanup fallback for a transient error.
      }
    }
    return true
  }

  async function removeExpenseItem(id: string): Promise<boolean> {
    const receipt = receiptByItemId(id)
    if (receipt) return removeReceipt(receipt.localId)
    removeItem(id)
    return true
  }

  function abortReceiptOperations(): void {
    receiptOperationVersion += 1
    for (const controller of receiptControllers) controller.abort()
    receiptControllers.clear()
  }

  function reset(): void {
    abortReceiptOperations()
    manualProject.value = false
    selectedProjectId.value = null
    manualProjectText.value = ''
    Object.assign(trip, {
      tripType: 'business' as TripType,
      startDate: '',
      startTime: '09:00',
      endDate: '',
      endTime: '18:00',
      policyConfirmed: false,
      confirmedEffectiveDays: '',
      noSubsidyException: false,
    })
    items.value = []
    includeSubsidy.value = false
    receiptFiles.value = []
    ocrUnavailable.value = false
    totals.value = null
    calculatedSignature.value = ''
    calculating.value = false
    calculationError.value = ''
    calculationVersion += 1
  }

  return {
    manualProject,
    selectedProjectId,
    manualProjectText,
    trip,
    items,
    sortedItems,
    includeSubsidy,
    receiptFiles,
    receiptUploadLimits,
    receiptBusy,
    ocrUnavailable,
    categories,
    categoriesLoading,
    categoryLoadError,
    totals,
    calculating,
    calculationError,
    requiresPolicyConfirmation,
    projectCalendarDays,
    projectPolicyType,
    maxExpenseItems,
    policyInputError,
    itemReadinessError,
    manualCategories,
    localReceiptCount,
    displayExpenseTotal,
    displaySubsidyTotal,
    displayReceiptCount,
    displayTotal,
    calculationsCurrent,
    excelDisabledReason,
    tripPayload,
    loadCategories,
    upsertManualItem,
    removeItem,
    removeExpenseItem,
    refreshCalculations,
    projectPayload,
    buildExcelPayload,
    setTripType,
    setSubsidyIncluded,
    addReceiptFiles,
    setReceiptUploadLimits,
    setExpenseItemLimit,
    retryReceipt,
    receiptByItemId,
    removeReceipt,
    reset,
  }
})
