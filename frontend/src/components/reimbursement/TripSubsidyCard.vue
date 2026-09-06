<script setup lang="ts">
import { useExpenseStore } from '@/stores/expense'
import type { TripType } from '@/types/expenses'

const expense = useExpenseStore()

const tripTypes: Array<{ id: TripType; name: string }> = [
  { id: 'business', name: '商务出差' },
  { id: 'project', name: '市外项目' },
  { id: 'same_city_project', name: '同市项目' },
  { id: 'internal', name: '公司内部出差' },
]
</script>

<template>
  <el-card
    shadow="never"
    class="content-card reimbursement-card"
  >
    <template #header>
      <strong>出差补助（可选）</strong>
    </template>
    <el-form label-position="top">
      <el-form-item label="是否申请出差补助">
        <el-switch
          :model-value="expense.includeSubsidy"
          active-text="申请出差补助"
          inactive-text="不申请"
          @update:model-value="expense.setSubsidyIncluded"
        />
      </el-form-item>
      <el-alert
        v-if="!expense.includeSubsidy"
        title="本报销单不申请出差补助"
        description="无需填写出差类型和出发、返回时间；系统只汇总费用明细。"
        type="info"
        :closable="false"
      />
      <template v-else>
        <el-form-item label="出差类型">
          <el-select
            :model-value="expense.trip.tripType"
            class="full-width"
            @update:model-value="expense.setTripType"
          >
            <el-option
              v-for="type in tripTypes"
              :key="type.id"
              :label="type.name"
              :value="type.id"
            />
          </el-select>
        </el-form-item>
        <div class="trip-grid">
          <el-form-item label="出发日期">
            <el-date-picker
              v-model="expense.trip.startDate"
              type="date"
              value-format="YYYY-MM-DD"
              class="full-width"
            />
          </el-form-item>
          <el-form-item label="出发时间">
            <el-time-picker
              v-model="expense.trip.startTime"
              format="HH:mm"
              value-format="HH:mm"
              class="full-width"
            />
          </el-form-item>
          <el-form-item label="返回日期">
            <el-date-picker
              v-model="expense.trip.endDate"
              type="date"
              value-format="YYYY-MM-DD"
              class="full-width"
            />
          </el-form-item>
          <el-form-item label="返回时间">
            <el-time-picker
              v-model="expense.trip.endTime"
              format="HH:mm"
              value-format="HH:mm"
              class="full-width"
            />
          </el-form-item>
        </div>
        <el-alert
          v-if="expense.trip.tripType === 'project' && expense.projectPolicyType"
          :title="expense.projectPolicyType === 'long_term_project'
            ? `系统识别为市外长期项目（${expense.projectCalendarDays} 个自然日）`
            : `系统识别为市外短期项目（${expense.projectCalendarDays} 个自然日）`"
          :description="expense.projectPolicyType === 'long_term_project'
            ? '超过 30 个自然日，使用长期标准并按半天规则自动计算。'
            : '30 个自然日以内（含），使用短期标准并自动计算。'"
          type="info"
          :closable="false"
          class="calculation-alert"
        />
        <el-alert
          v-if="!expense.requiresPolicyConfirmation"
          title="自动计算规则"
          description="出发日 12:00 前计 1 天、12:00（含）以后计 0.5 天；返回日 12:00 前计 0.5 天、12:00（含）以后计 1 天。同日从 12:00 前跨至 12:00（含）以后计 1 天，否则计 0.5 天。"
          type="info"
          :closable="false"
        />
        <div
          v-else
          class="policy-confirmation"
        >
          <el-alert
            title="该类型不自动推断政策例外"
            description="请按公司现行制度确认本次有效天数；每日标准由管理员统一配置，员工不能修改。"
            type="warning"
            :closable="false"
          />
          <div class="policy-fields">
            <el-form-item label="确认有效天数（0.5 天为单位）">
              <el-input
                v-model="expense.trip.confirmedEffectiveDays"
                inputmode="decimal"
                placeholder="例如 8.0"
                maxlength="5"
              />
            </el-form-item>
          </div>
          <el-checkbox v-model="expense.trip.policyConfirmed">
            我已按公司现行制度确认以上有效天数
          </el-checkbox>
          <el-checkbox
            v-if="expense.trip.tripType === 'internal'"
            v-model="expense.trip.noSubsidyException"
          >
            本次适用不补助例外（例如无锡—苏州）
          </el-checkbox>
          <p
            v-if="expense.policyInputError"
            class="field-error"
          >
            {{ expense.policyInputError }}
          </p>
        </div>
      </template>
      <div
        v-if="expense.includeSubsidy && expense.totals?.subsidy"
        class="subsidy-preview"
        role="status"
        aria-live="polite"
      >
        <span>自然日 {{ expense.totals.subsidy.calendarDays }} 天</span>
        <span>有效 {{ expense.totals.subsidy.effectiveDays }} 天</span>
        <span>¥{{ expense.totals.subsidy.dailyRate }} / 天</span>
        <strong>补助 ¥{{ expense.totals.subsidy.total }}</strong>
      </div>
    </el-form>
  </el-card>
</template>
