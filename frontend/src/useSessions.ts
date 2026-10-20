/**
 * 会话状态（模块级单例 store）。
 *
 * 为什么 sessionId 必须提出来：它以前锁在 ChatPanel 内部，左侧会话列表是兄弟
 * 组件，根本拿不到——列表要高亮当前会话、删除会话要判断是不是正在聊的这个。
 * 会话是"应用级"状态，不是某个面板的内部状态（与后端"进程级单例"是同一个判断）。
 *
 * 切换/新建只改 sessionId；ChatPanel watch 它来拉历史、中止在途请求——
 * store 不负责恢复逻辑，职责边界保持单向。
 */
import { ref, watch } from 'vue'
import { deleteSession, fetchSessions } from './api'
import type { SessionItem } from './types'

const KEY = 'jobpilot.session_id'

export function newSessionId(): string {
  return `web-${Math.random().toString(36).slice(2, 8)}`
}

// 应用级会话状态：刷新后从 localStorage 接上；没有就现造一个（首次进入即可聊）
const sessionId = ref(localStorage.getItem(KEY) || newSessionId())
localStorage.setItem(KEY, sessionId.value)
const sessions = ref<SessionItem[]>([])
const loaded = ref(false)

watch(sessionId, (v) => localStorage.setItem(KEY, v))

async function load(): Promise<void> {
  try {
    sessions.value = await fetchSessions()
    loaded.value = true
  } catch {
    /* 列表接口不可用静默降级：聊天主流程不依赖它 */
  }
}

/** 新建会话：只换 id，清场由 ChatPanel 的 watch 做 */
function create(): string {
  sessionId.value = newSessionId()
  return sessionId.value
}

function select(id: string): void {
  if (id !== sessionId.value) sessionId.value = id
}

async function remove(id: string): Promise<void> {
  await deleteSession(id)
  // 删的是当前会话：先换新 id（watch 清场），再刷新列表
  if (id === sessionId.value) sessionId.value = newSessionId()
  await load()
}

export function useSessions() {
  return { sessionId, sessions, loaded, load, create, select, remove, newSessionId }
}
