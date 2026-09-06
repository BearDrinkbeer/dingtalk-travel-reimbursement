<script setup lang="ts">
import OaTemplateSettingsCard from '@/components/settings/OaTemplateSettingsCard.vue'
import ReceiptKeywordSettingsCard from '@/components/settings/ReceiptKeywordSettingsCard.vue'
import SubsidyRateSettingsCard from '@/components/settings/SubsidyRateSettingsCard.vue'
</script>

<template>
  <main class="page-shell narrow-page">
    <div class="admin-page-header">
      <div>
        <nav
          class="admin-links admin-page-nav"
          aria-label="管理员功能"
        >
          <RouterLink to="/">
            报销单
          </RouterLink>
          <RouterLink
            to="/admin/settings"
            aria-current="page"
          >
            系统设置
          </RouterLink>
        </nav>
        <h1>系统设置</h1>
      </div>
    </div>

    <ReceiptKeywordSettingsCard />
    <SubsidyRateSettingsCard />
    <OaTemplateSettingsCard />
  </main>
</template>
