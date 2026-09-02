import { createApp } from 'vue'
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-message-box.css'

import App from './App.vue'
import { pinia } from './pinia'
import router from './router'
import './styles/main.css'
import { initializeDingTalkRemoteDebug } from './utils/dingtalkRemoteDebug'

void initializeDingTalkRemoteDebug().catch((error: unknown) => {
  // Debug instrumentation must never prevent the reimbursement page from loading.
  console.warn('钉钉 H5 远程调试工具初始化失败', error)
})

createApp(App).use(pinia).use(router).mount('#app')
