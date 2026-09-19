<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { fetchHistory, streamChat } from '../api'
import { renderMarkdown } from '../markdown'
import type { ChatMessage, SourceRef } from '../types'

const emit = defineEmits<{ (e: 'kb-changed'): void }>()

const messages = ref<ChatMessage[]>([])
const input = ref('')
const streaming = ref(false)
const intent = ref('')
const status = ref('')
const error = ref('')
const listEl = ref<HTMLElement | null>(null)

// 功能模式：auto 交给后端关键词路由；显式选择则跳过路由（确定性）
const MODES = [
  { value: 'auto', label: '自动' },
  { value: 'jd_analysis', label: 'JD 分析' },
  { value: 'resume_advice', label: '简历优化' },
  { value: 'mock_interview', label: '模拟面试' },
] as const
const mode = ref<string>('auto')

const sessionId = `web-${Math.random().toString(36).slice(2, 8)}`

// 正在等待首个增量（阶段状态/首 token），此时显示状态行而不是空气泡
const waitingFirstToken = computed(() => {
  const last = messages.value[messages.value.length - 1]
  return streaming.value && last?.role === 'assistant' && last.content === ''
})

const SUGGESTIONS = [
  '分析这个 JD：负责基于大模型的 Agent 应用研发，要求熟悉 RAG、LangChain、Prompt 工程',
  '帮我看看简历里项目经历有什么可以优化的',
  '我想模拟一场 Python 后端的技术面试',
]

onMounted(async () => {
  messages.value = await fetchHistory(sessionId)
  scrollToBottom()
})

async function send(text?: string) {
  const content = (text ?? input.value).trim()
  if (!content || streaming.value) return
  input.value = ''
  error.value = ''
  status.value = ''
  streaming.value = true
  messages.value.push({ role: 'user', content })

  // 必须是 reactive 对象：流式期间逐字改 content 才能触发视图更新
  // （普通对象 push 进数组后，直接改它不会触发响应式——这是真实的踩坑点）
  const assistant = reactive<ChatMessage>({ role: 'assistant', content: '' })
  messages.value.push(assistant)
  scrollToBottom()

  try {
    await streamChat(sessionId, content, {
      onDelta: (t) => {
        status.value = ''
        assistant.content += t
        scrollToBottom()
      },
      onStatus: (s) => {
        status.value = s
        scrollToBottom()
      },
      onDone: (d) => {
        intent.value = d.intent
        // 面试官等 Agent 会读知识库，入库状态可能变化，这里暂不联动，留给知识库面板
      },
      onArtifacts: (a) => {
        // 检索命中的原文片段：让回答可追溯（面试演示的关键卖点）
        if (Array.isArray(a.sources)) {
          assistant.sources = a.sources as SourceRef[]
        }
      },
      onError: (m) => {
        error.value = m
      },
    }, undefined, mode.value)
    if (!assistant.content && !error.value) {
      assistant.content = '（空回复，请检查后端日志）'
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    streaming.value = false
    status.value = ''
    scrollToBottom()
  }
}

function scrollToBottom() {
  nextTick(() => {
    listEl.value?.scrollTo({ top: listEl.value.scrollHeight })
  })
}

// AI 回复走 markdown + DOMPurify 净化；用户输入永远按纯文本展示（不可信输入）。
// 流式期间每条 delta 都会整段重解析，marked 对此足够快；若后续消息很长可再做节流。
function rendered(content: string): string {
  return renderMarkdown(content)
}
</script>

<template>
  <section class="chat card">
    <div ref="listEl" class="messages">
      <div v-if="messages.length === 0" class="empty">
        <h2>你的求职 Copilot</h2>
        <p>JD 分析 / 简历优化 / 模拟面试，直接开口。</p>
        <div class="suggestions">
          <button
            v-for="s in SUGGESTIONS"
            :key="s"
            class="ghost suggestion"
            @click="send(s)"
          >{{ s }}</button>
        </div>
      </div>

      <div
        v-for="(m, i) in messages"
        :key="i"
        class="bubble-row"
        :class="m.role"
      >
        <div class="bubble-wrap">
          <div v-if="m.role === 'assistant'" class="bubble md" v-html="rendered(m.content)" />
          <div v-else class="bubble"><pre>{{ m.content }}</pre></div>

          <details v-if="m.sources && m.sources.length" class="sources">
            <summary>引用来源（{{ m.sources.length }} 段原文）</summary>
            <div v-for="(s, j) in m.sources" :key="j" class="source-item">
              <div class="source-meta">
                <span class="cat">{{ s.category }}</span>
                <span>{{ s.source }}</span>
                <span class="dist">距离 {{ s.distance.toFixed(3) }}</span>
              </div>
              <p>{{ s.text }}</p>
            </div>
          </details>
        </div>
      </div>

      <div v-if="waitingFirstToken" class="typing">
        {{ status || '正在生成' }}<span class="dots">…</span>
      </div>
    </div>

    <div v-if="error" class="error">⚠ {{ error }}</div>

    <div class="modes">
      <button
        v-for="m in MODES"
        :key="m.value"
        class="mode-chip"
        :class="{ active: mode === m.value }"
        :disabled="streaming"
        @click="mode = m.value"
      >{{ m.label }}</button>
      <span v-if="mode === 'mock_interview'" class="mode-tip">面试中直接回答即可；说「结束面试」退出</span>
    </div>

    <div class="composer">
      <textarea
        v-model="input"
        rows="2"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        :disabled="streaming"
        @keydown.enter.exact.prevent="send()"
      />
      <button :disabled="streaming || !input.trim()" @click="send()">
        {{ streaming ? '生成中…' : '发送' }}
      </button>
    </div>

    <div v-if="intent" class="intent">本次路由意图：{{ intent }}</div>
  </section>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  min-height: 0;
}
.empty { text-align: center; color: var(--text-2); padding-top: 8vh; }
.empty h2 { color: var(--text); margin-bottom: 4px; }
.suggestions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 560px;
  margin: 20px auto 0;
}
.suggestion { text-align: left; padding: 10px 14px; }

.bubble-row { display: flex; margin-bottom: 14px; }
.bubble-row.user { justify-content: flex-end; }
.bubble-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 78%;
  min-width: 0;
}
.bubble-row.user .bubble-wrap { align-items: flex-end; }
.bubble {
  max-width: 100%;
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--primary-weak);
}
.bubble-row.user .bubble {
  background: var(--primary);
  color: #fff;
}

/* 引用来源：可折叠，回答可追溯到原文片段 */
.sources {
  width: 100%;
  font-size: 12px;
  color: var(--text-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
  background: var(--panel);
}
.sources summary { cursor: pointer; user-select: none; }
.source-item { margin-top: 8px; }
.source-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 11px;
  color: var(--text-2);
}
.source-meta .cat {
  background: var(--primary-weak);
  color: var(--primary);
  padding: 0 6px;
  border-radius: 5px;
}
.source-meta .dist { margin-left: auto; }
.source-item p {
  margin: 4px 0 0;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
.bubble pre {
  margin: 0;
  font: inherit;
  white-space: pre-wrap;
  word-break: break-word;
}

/* AI 气泡的 markdown 排版（v-html 内容需 :deep 穿透） */
.bubble.md :deep(p) { margin: 0 0 8px; }
.bubble.md :deep(p:last-child) { margin-bottom: 0; }
.bubble.md :deep(h1),
.bubble.md :deep(h2),
.bubble.md :deep(h3),
.bubble.md :deep(h4) {
  margin: 14px 0 6px;
  font-size: 1.05em;
  line-height: 1.4;
}
.bubble.md :deep(ul),
.bubble.md :deep(ol) { margin: 6px 0 8px; padding-left: 20px; }
.bubble.md :deep(li) { margin: 3px 0; }
.bubble.md :deep(blockquote) {
  margin: 8px 0;
  padding: 4px 12px;
  border-left: 3px solid var(--primary);
  background: rgba(59, 110, 245, 0.06);
  border-radius: 4px;
  color: var(--text-2);
}
.bubble.md :deep(blockquote p) { margin: 2px 0; }
.bubble.md :deep(code) {
  font-family: Consolas, 'JetBrains Mono', monospace;
  font-size: 0.92em;
  background: rgba(31, 35, 41, 0.08);
  padding: 1px 5px;
  border-radius: 4px;
}
.bubble.md :deep(pre) {
  margin: 8px 0;
  padding: 12px 14px;
  background: #f6f8fa;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow-x: auto;
}
.bubble.md :deep(pre code) {
  background: none;
  padding: 0;
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre;
}
.bubble.md :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;
  width: 100%;
}
.bubble.md :deep(th),
.bubble.md :deep(td) {
  border: 1px solid var(--border);
  padding: 5px 10px;
  text-align: left;
}
.bubble.md :deep(th) { background: var(--bg); font-weight: 600; }
.bubble.md :deep(a) { color: var(--primary); }
.bubble.md :deep(hr) { border: none; border-top: 1px solid var(--border); margin: 10px 0; }
.typing { color: var(--text-2); font-size: 13px; }

.error {
  margin: 0 20px 8px;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: #fdeceb;
  color: var(--danger);
  font-size: 13px;
}

.modes {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px 0;
}
.mode-chip {
  background: var(--bg);
  color: var(--text-2);
  padding: 4px 12px;
  font-size: 12px;
  border-radius: 999px;
}
.mode-chip.active {
  background: var(--primary);
  color: #fff;
  font-weight: 600;
}
.mode-tip { font-size: 11px; color: var(--text-2); margin-left: 4px; }

.composer {
  display: flex;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--border);
}
.composer textarea { flex: 1; resize: none; }
.composer button { align-self: flex-end; }
.intent {
  padding: 0 20px 10px;
  font-size: 12px;
  color: var(--text-2);
}
</style>
