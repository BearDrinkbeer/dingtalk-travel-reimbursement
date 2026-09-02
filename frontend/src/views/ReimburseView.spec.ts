import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import { fetchReadiness } from '@/api/health'
import { calculateTotals } from '@/api/expenses'
import { searchProjects } from '@/api/projects'
import { useAuthStore } from '@/stores/auth'
import { useExpenseStore } from '@/stores/expense'
import ReimburseView from './ReimburseView.vue'

vi.mock('@/api/health', () => ({
  fetchReadiness: vi.fn(),
}))

vi.mock('@/api/projects', () => ({
  searchProjects: vi.fn(),
}))

vi.mock('@/api/expenses', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/expenses')>()
  return {
    ...actual,
    calculateTotals: vi.fn(),
  }
})

function formInput(label: string): DOMWrapper<HTMLInputElement> {
  const item = [...document.querySelectorAll<HTMLElement>('.el-form-item')].find(
    (candidate) => candidate.querySelector('.el-form-item__label')?.textContent?.trim() === label,
  )
  const input = item?.querySelector<HTMLInputElement>('input, textarea')
  if (!input) throw new Error(`Missing form input: ${label}`)
  return new DOMWrapper(input)
}

function button(label: string): DOMWrapper<HTMLButtonElement> {
  const element = [...document.querySelectorAll<HTMLButtonElement>('button')].find(
    (candidate) => candidate.textContent?.trim() === label,
  )
  if (!element) throw new Error(`Missing visible button: ${label}`)
  return new DOMWrapper(element)
}

describe('ReimburseView manual item editor', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="test-app"></div>'
    window.ResizeObserver = class ResizeObserver {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
    window.requestAnimationFrame = (callback: FrameRequestCallback) => {
      callback(0)
      return 0
    }
    window.cancelAnimationFrame = () => undefined
    vi.mocked(fetchReadiness).mockResolvedValue({
      status: 'ready',
      checks: {
        database: 'ok',
        excelTemplate: 'ok',
        tempStorage: 'ok',
        ocr: 'disabled',
      },
    })
    vi.mocked(searchProjects).mockResolvedValue([])
    vi.mocked(calculateTotals).mockResolvedValue({
      expenseTotal: '0.00',
      subsidyTotal: '0.00',
      totalAmount: '0.00',
      receiptCount: 0,
      uppercaseAmount: '零元整',
      subsidy: null,
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.restoreAllMocks()
    Reflect.deleteProperty(URL, 'createObjectURL')
    Reflect.deleteProperty(URL, 'revokeObjectURL')
  })

  it('keeps four receipts when reopening an item and editing only description and amount', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.status = 'authenticated'
    auth.session = {
      user: { userId: 'synthetic-user', name: '测试用户' },
      departments: [{ id: '100', name: '测试部门' }],
      selectedDepartment: { id: '100', name: '测试部门' },
      isAdmin: false,
      csrfToken: 'synthetic-csrf',
    }
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'local_transport', name: '市内交通费', order: 1, manualSelectable: true },
    ]

    const wrapper = mount(ReimburseView, {
      attachTo: '#test-app',
      global: {
        plugins: [pinia, ElementPlus],
        stubs: {
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('OCR 结果会直接填入，发现不准确时直接编辑')
    expect(wrapper.text()).not.toContain('确认并加入明细')
    expect(wrapper.text()).not.toContain('全部加入明细')

    await button('手动添加').trigger('click')
    await nextTick()
    await formInput('发生日期').setValue('2026-07-01')
    await formInput('说明').setValue('市内交通')
    await formInput('金额（元）').setValue('44.89')
    const receiptCountInput = formInput('票据张数')
    receiptCountInput.element.value = '4'
    await receiptCountInput.trigger('input')
    await receiptCountInput.trigger('blur')
    await button('保存').trigger('click')
    await nextTick()

    expect(expense.items).toHaveLength(1)
    expect(expense.items[0]).toMatchObject({ receiptCount: 4 })

    await button('编辑').trigger('click')
    await nextTick()
    expect(formInput('票据张数').element.value).toBe('4')

    await formInput('说明').setValue('修改后的市内交通')
    await formInput('金额（元）').setValue('45.89')
    await button('保存').trigger('click')
    await nextTick()

    expect(expense.items[0]).toMatchObject({
      description: '修改后的市内交通',
      amount: '45.89',
      receiptCount: 4,
    })

    wrapper.unmount()
  })

  it('previews the original selected receipt with a short-lived object URL', async () => {
    // Keep the iframe same-origin in jsdom 30. The assertion still verifies that
    // production code obtains and later revokes the browser-managed object URL.
    const createObjectUrl = vi.fn(() => 'http://localhost/receipt-preview')
    const revokeObjectUrl = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectUrl })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectUrl })
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.status = 'authenticated'
    auth.session = {
      user: { userId: 'synthetic-user', name: '测试用户' },
      departments: [{ id: '100', name: '测试部门' }],
      selectedDepartment: { id: '100', name: '测试部门' },
      isAdmin: false,
      csrfToken: 'synthetic-csrf',
    }
    const expense = useExpenseStore()
    expense.categories = [
      { id: 'local_transport', name: '市内交通费', order: 1, manualSelectable: true },
    ]
    const receipt = new File(['synthetic pdf'], '测试票据.pdf', { type: 'application/pdf' })
    expense.receiptFiles.push({
      localId: 'receipt-1',
      file: receipt,
      tempId: 'temp-1',
      name: receipt.name,
      size: receipt.size,
      uploadProgress: 100,
      status: 'done',
      ocrItemId: 'ocr-temp-1',
    })
    expense.items.push({
      id: 'ocr-temp-1',
      category: 'local_transport',
      date: '2026-07-06',
      displayDate: '2026-07-06',
      description: '起点-终点',
      amount: '10.13',
      receiptCount: 1,
      source: 'ocr',
      confidence: '0.90',
      warnings: [],
    })

    const wrapper = mount(ReimburseView, {
      attachTo: '#test-app',
      global: {
        plugins: [pinia, ElementPlus],
        stubs: {
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('OCR')
    expect(wrapper.text()).not.toContain('OCR 90%')

    await button('测试票据.pdf · 预览').trigger('click')
    await nextTick()

    expect(createObjectUrl).toHaveBeenCalledWith(receipt)
    expect(document.body.textContent).toContain('票据预览：测试票据.pdf')
    expect(document.querySelector('iframe[title="测试票据.pdf 预览"]')?.getAttribute('src'))
      .toBe('http://localhost/receipt-preview')

    wrapper.unmount()
    expect(revokeObjectUrl).toHaveBeenCalledWith('http://localhost/receipt-preview')
  })
})
