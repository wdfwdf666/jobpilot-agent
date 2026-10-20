<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useSessions } from '../useSessions'

/**
 * 左侧会话列表：新建 / 切换恢复 / 删除。
 *
 * 只负责改 store 里的 sessionId 和调删除接口；切走之后"拉历史、清场、
 * 中止在途流"都由 ChatPanel 的 watch 做——列表不碰聊天状态，避免两边
 * 各清一半的竞态。
 */
const { sessionId, sessions, load, create, select, remove } = useSessions()
const busy = ref(false)

onMounted(load)

async function onRemove(id: string, title: string) {
  if (busy.value) return
  if (!confirm(`删除会话「${title}」？该会话的消息与面试记录将一并清除。`)) return
  busy.value = true
  try {
    await remove(id)
  } finally {
    busy.value = false
  }
}

/** 相对时间：今天显示时:分，昨天内显示"昨天"，更早显示日期 */
function relativeTime(value: string): string {
  if (!value) return ''
  const t = new Date(value.replace(' ', 'T'))
  if (Number.isNaN(t.getTime())) return value
  const now = new Date()
  const sameDay = t.toDateString() === now.toDateString()
  if (sameDay) {
    return `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`
  }
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (t.toDateString() === yesterday.toDateString()) return '昨天'
  return `${t.getMonth() + 1}-${t.getDate()}`
}
</script>

<template>
  <section class="sessions card">
    <header class="head">
      <h3>会话</h3>
      <button class="ghost sm" @click="create">＋ 新建</button>
    </header>

    <div class="list">
      <div
        v-for="s in sessions"
        :key="s.session_id"
        class="item"
        :class="{ active: s.session_id === sessionId }"
        @click="select(s.session_id)"
      >
        <span v-if="s.kind === '面试'" class="badge">面试</span>
        <span class="title" :title="s.title">{{ s.title }}</span>
        <span class="time">{{ relativeTime(s.updated_at) }}</span>
        <button
          class="del"
          title="删除会话"
          @click.stop="onRemove(s.session_id, s.title)"
        >×</button>
      </div>
      <div v-if="!sessions.length" class="empty">还没有会话记录</div>
    </div>
  </section>
</template>

<style scoped>
.sessions {
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
  max-height: 260px;
  padding: 12px 14px;
  min-height: 0;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.head h3 { margin: 0; font-size: 14px; }
.sm { padding: 2px 8px; font-size: 12px; }

.list {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text);
}
.item:hover { background: var(--bg); }
.item.active {
  background: var(--primary-weak);
  color: var(--primary);
  font-weight: 600;
}
.badge {
  flex: 0 0 auto;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--primary);
  color: #fff;
}
.title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.time {
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--text-2);
}
.del {
  flex: 0 0 auto;
  border: none;
  background: none;
  color: var(--text-2);
  font-size: 14px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 4px;
  cursor: pointer;
  visibility: hidden;
}
.item:hover .del { visibility: visible; }
.del:hover { color: var(--danger); background: rgba(0, 0, 0, 0.05); }
.empty {
  text-align: center;
  color: var(--text-2);
  font-size: 12px;
  padding: 16px 0;
}
</style>
