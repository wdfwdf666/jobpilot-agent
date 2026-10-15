<script setup lang="ts">
import { ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import KBPanel from './components/KBPanel.vue'

const kbRefreshKey = ref(0)
function onKbChanged() {
  kbRefreshKey.value++
}

// 面试报告里的"待补强"chips -> 知识库检索面板。
// 组件间不直接互相调用，靠 App 这一层传话，保证两侧都能独立单测/复用。
const kbPanel = ref<InstanceType<typeof KBPanel> | null>(null)
function onKbSearch(query: string) {
  kbPanel.value?.runSearch(query)
}
</script>

<template>
  <div class="layout">
    <header class="topbar">
      <div class="brand">JobPilot<span class="sub">智能求职助手 Agent</span></div>
      <div class="meta">Vue 3 + FastAPI · SSE 流式 · LangGraph 人审</div>
    </header>
    <main class="content">
      <KBPanel ref="kbPanel" :refresh-key="kbRefreshKey" @changed="onKbChanged" />
      <ChatPanel @kb-changed="onKbChanged" @kb-search="onKbSearch" />
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.topbar {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 12px 24px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}
.brand {
  font-size: 18px;
  font-weight: 700;
  color: var(--primary);
}
.brand .sub {
  margin-left: 10px;
  font-size: 13px;
  font-weight: 400;
  color: var(--text-2);
}
.meta {
  font-size: 12px;
  color: var(--text-2);
}
.content {
  flex: 1;
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: 16px;
  padding: 16px 24px;
  min-height: 0;
}
@media (max-width: 900px) {
  .content { grid-template-columns: 1fr; }
}
</style>
