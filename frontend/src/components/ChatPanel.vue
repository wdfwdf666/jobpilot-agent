<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { fetchHistory, streamChat } from '../api'
import type { ChatMessage } from '../types'

const emit = defineEmits<{ (e: 'kb-changed'): void }>()

const messages = ref<ChatMessage[]>([])
const input = ref('')
const streaming = ref(false)
const intent = ref('')
const error = ref('')
const listEl = ref<HTMLElement | null>(null)

const sessionId = `web-${Math.random().toString(36).slice(2, 8)}`

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
  streaming.value = true
  messages.value.push({ role: 'user', content })

  const assistant = { role: 'assistant' as const, content: '' }
  messages.value.push(assistant)
  scrollToBottom()

  try {
    await streamChat(sessionId, content, {
      onDelta: (t) => {
        assistant.content += t
        scrollToBottom()
      },
      onDone: (d) => {
        intent.value = d.intent
        // 面试官等 Agent 会读知识库，入库状态可能变化，这里暂不联动，留给知识库面板
      },
      onError: (m) => {
        error.value = m
      },
    })
    if (!assistant.content && !error.value) {
      assistant.content = '（空回复，请检查后端日志）'
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    streaming.value = false
    scrollToBottom()
  }
}

function scrollToBottom() {
  nextTick(() => {
    listEl.value?.scrollTo({ top: listEl.value.scrollHeight })
  })
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
        <div class="bubble">
          <pre>{{ m.content }}</pre>
        </div>
      </div>

      <div v-if="streaming" class="typing">正在生成<span class="dots">…</span></div>
    </div>

    <div v-if="error" class="error">⚠ {{ error }}</div>

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
.bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 12px;
  background: var(--primary-weak);
}
.bubble-row.user .bubble {
  background: var(--primary);
  color: #fff;
}
.bubble pre {
  margin: 0;
  font: inherit;
  white-space: pre-wrap;
  word-break: break-word;
}
.typing { color: var(--text-2); font-size: 13px; }

.error {
  margin: 0 20px 8px;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: #fdeceb;
  color: var(--danger);
  font-size: 13px;
}

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
