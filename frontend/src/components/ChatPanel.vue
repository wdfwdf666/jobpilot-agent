<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import {
  endInterview,
  fetchChatState,
  fetchHistory,
  fetchInterviewReport,
  resumeChat,
  streamChat,
} from '../api'
import { renderMarkdown } from '../markdown'
import type {
  ChatMessage,
  InterviewReport,
  ReviewDecision,
  SourceRef,
  SseArtifacts,
  StreamHandlers,
} from '../types'
import { useSessions } from '../useSessions'
import MatchPanel from './MatchPanel.vue'
import ReviewCard from './ReviewCard.vue'

const emit = defineEmits<{
  (e: 'kb-changed'): void
  (e: 'kb-search', query: string): void
}>()

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

// 会话 id 是应用级状态（useSessions 模块级单例）：左侧列表与聊天面板共享同一个
// id，切换/新建由列表驱动，本组件只 watch 它来清场并从检查点恢复历史。
// 图状态与对话历史都在后端检查点里按 thread_id 存着，只要 id 不变，
// 刷新页面甚至重启后端都能接着聊（此前是内存 dict，刷新即丢）。
const { sessionId, load: loadSessions, create: createSession } = useSessions()

// 在途请求的中止句柄：切会话时先 abort 再恢复，否则正在流的那场
// 会把后续 token 灌进新会话的气泡里（串流是切会话最典型的竞态）。
let abort: AbortController | null = null

const hadInterview = ref(false)
const report = ref<InterviewReport | null>(null)
const reportOpen = ref(false)
const reportBusy = ref(false)

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

onMounted(() => {
  loadSessions() // 列表数据由 SessionList 拉取，这里只是兜底早一步触发
  restore()
})

// 会话切换（列表点选 / 新建 / 当前会话被删）：中止在途流 -> 清场 -> 恢复
watch(sessionId, () => {
  abort?.abort()
  abort = null
  messages.value = []
  intent.value = ''
  error.value = ''
  report.value = null
  reportOpen.value = false
  hadInterview.value = false
  restore()
})

/** 刷新后从检查点恢复：历史消息 + 上次的结构化产物 + 待人工确认的卡片。 */
async function restore() {
  const sid = sessionId.value // 恢复期间可能又被切走：以快照为准，写前校验
  messages.value = (await fetchHistory(sid)) as ChatMessage[]
  if (sessionId.value !== sid) return // 已切走：丢弃本次恢复结果
  try {
    const state = await fetchChatState(sid)
    if (sessionId.value !== sid) return
    const last = messages.value[messages.value.length - 1]
    if (state.artifacts && Object.keys(state.artifacts).length) {
      if (last && last.role === 'assistant') {
        applyArtifacts(last, state.artifacts)
      } else {
        // 中断发生在生成回复之前：补一条空气泡承载审核卡片
        const bubble = reactive<ChatMessage>({ role: 'assistant', content: '' })
        applyArtifacts(bubble, state.artifacts)
        messages.value.push(bubble)
      }
    }
    if (state.interrupt) {
      const target = [...messages.value].reverse().find((m) => m.role === 'assistant')
      if (target) target.review = state.interrupt
    }
  } catch {
    /* 状态接口不可用时静默降级：历史仍可显示 */
  }
  scrollToBottom()
}

function applyArtifacts(target: ChatMessage, a: SseArtifacts) {
  if (Array.isArray(a.sources)) target.sources = a.sources as SourceRef[]
  // 结构化产物优先于正文渲染：后端已经把 match_result 推过来了，
  // 之前只消费 sources，等于白干了一半（匹配度让模型写进 markdown 里，看不清也点不动）
  if (a.match_result) target.matchResult = a.match_result
  if (a.jd_analysis) target.jdAnalysis = a.jd_analysis
  if (typeof a.edited_by_human === 'boolean') target.editedByHuman = a.edited_by_human
}

/** 统一的流式驱动：/chat/stream 与 /chat/resume 共用，续写同一条气泡。 */
async function pump(
  start: (handlers: StreamHandlers) => Promise<void>,
  assistant: ChatMessage,
) {
  streaming.value = true
  error.value = ''
  status.value = ''
  try {
    await start({
      onDelta: (t) => {
        status.value = ''
        assistant.content += t
        scrollToBottom()
      },
      onStatus: (s) => {
        status.value = s
        scrollToBottom()
      },
      onArtifacts: (a) => applyArtifacts(assistant, a),
      // 图在人工审核处挂起：本轮到此结束，等用户在卡片上做决定
      onInterrupt: (r) => {
        assistant.review = r
      },
      onDone: (d) => {
        intent.value = d.intent
        if (d.intent === 'mock_interview') hadInterview.value = true
      },
      onError: (m) => {
        error.value = m
      },
    })
    if (!assistant.content && !assistant.review && !error.value) {
      assistant.content = '（空回复，请检查后端日志）'
    }
  } catch (e) {
    // 切会话触发的 abort 是正常路径：不计为错误，也不往已清空的列表里写东西
    if (e instanceof DOMException && e.name === 'AbortError') return
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    streaming.value = false
    status.value = ''
    scrollToBottom()
  }
}

async function send(text?: string) {
  const content = (text ?? input.value).trim()
  if (!content || streaming.value) return
  input.value = ''
  messages.value.push({ role: 'user', content })

  // 必须是 reactive 对象：流式期间逐字改 content 才能触发视图更新
  // （普通对象 push 进数组后，直接改它不会触发响应式——这是真实的踩坑点）
  const assistant = reactive<ChatMessage>({ role: 'assistant', content: '' })
  messages.value.push(assistant)
  scrollToBottom()

  abort = new AbortController()
  await pump(
    (h) => streamChat(sessionId.value, content, h, abort!.signal, mode.value),
    assistant,
  )
}

/** 提交人工审核决定：用同一个 thread 续跑，结果续写到同一条气泡。 */
async function submitReview(assistant: ChatMessage, decision: ReviewDecision) {
  if (streaming.value) return
  assistant.reviewDecision = decision
  abort = new AbortController()
  await pump((h) => resumeChat(sessionId.value, decision, h, abort!.signal), assistant)
}

async function switchSession() {
  // 新建会话：清场交给 watch(sessionId)（id 变化必触发，包括列表驱动的切换）
  if (streaming.value) return
  createSession()
}

async function openReport() {
  if (reportBusy.value) return
  reportBusy.value = true
  error.value = ''
  try {
    report.value = await fetchInterviewReport(sessionId.value)
    reportOpen.value = true
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    reportBusy.value = false
  }
}

async function finishInterview() {
  if (streaming.value) return
  await endInterview(sessionId.value)
  await openReport()
}

/** 报告既在页面里预览，也支持落成文件带走（简历演示时更实用）。 */
function downloadReport() {
  if (!report.value) return
  const stamp = report.value.generated_at.replace(/[: ]/g, '-')
  const blob = new Blob([report.value.markdown], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `模拟面试评分报告-${stamp}.md`
  a.click()
  URL.revokeObjectURL(url)
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

          <!-- 结构化产物优先展示：这才是 JD 分析的真正交付物 -->
          <MatchPanel
            v-if="m.matchResult"
            :jd-analysis="m.jdAnalysis"
            :match-result="m.matchResult"
            :edited-by-human="m.editedByHuman"
          />

          <ReviewCard
            v-if="m.review"
            :request="m.review"
            :decision="m.reviewDecision"
            :busy="streaming"
            @submit="(d) => submitReview(m, d)"
          />

          <details v-if="m.sources && m.sources.length" class="sources">
            <summary>引用来源（{{ m.sources.length }} 条）</summary>
            <div v-for="(s, j) in m.sources" :key="j" class="source-item">
              <div class="source-meta">
                <span class="cat">{{ s.category }}</span>
                <span>{{ s.source }}</span>
                <!-- 联网来源没有向量距离，distance 为 0 时不显示这一项 -->
                <span v-if="s.distance > 0" class="dist">距离 {{ s.distance.toFixed(3) }}</span>
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

    <!-- 面试评分报告：结束后聚合成 md，薄弱点可直接跳到知识库检索 -->
    <section v-if="reportOpen && report" class="report">
      <header class="report-head">
        <div>
          <strong>模拟面试评分报告</strong>
          <span class="report-meta">
            {{ report.turns }} 题 · 平均 {{ report.average_score }}/100 · {{ report.generated_at }}
          </span>
        </div>
        <div class="report-actions">
          <button class="ghost sm" @click="downloadReport">下载 .md</button>
          <button class="ghost sm" @click="reportOpen = false">收起</button>
        </div>
      </header>

      <div v-if="report.knowledge_gaps.length" class="gaps">
        <span class="gaps-label">待补强（点击跳到知识库检索）：</span>
        <button
          v-for="(g, i) in report.knowledge_gaps"
          :key="i"
          class="gap-chip"
          :title="g.missing_points.join('；')"
          @click="emit('kb-search', g.query)"
        >{{ g.label || g.question }}</button>
      </div>

      <div class="report-body md" v-html="rendered(report.markdown)" />
    </section>

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
      <span class="spacer" />
      <button class="ghost sm" :disabled="streaming" @click="switchSession">新会话</button>
      <button v-if="mode === 'mock_interview'" class="ghost sm" :disabled="streaming" @click="finishInterview">
        结束并出报告
      </button>
      <button v-else-if="hadInterview" class="ghost sm" :disabled="reportBusy" @click="openReport">
        导出面试报告
      </button>
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
/* 匹配度面板比气泡更宽：它承载表格化的结构化结果，挤在 78% 里读不动 */
.bubble-wrap:has(> .match-panel) { max-width: 100%; }

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
.bubble.md :deep(code),
.report-body.md :deep(code) {
  font-family: Consolas, 'JetBrains Mono', monospace;
  font-size: 0.92em;
  background: rgba(31, 35, 41, 0.08);
  padding: 1px 5px;
  border-radius: 4px;
}
.bubble.md :deep(pre),
.report-body.md :deep(pre) {
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
.bubble.md :deep(table),
.report-body.md :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;
  width: 100%;
}
.bubble.md :deep(th),
.bubble.md :deep(td),
.report-body.md :deep(th),
.report-body.md :deep(td) {
  border: 1px solid var(--border);
  padding: 5px 10px;
  text-align: left;
}
.bubble.md :deep(th),
.report-body.md :deep(th) { background: var(--bg); font-weight: 600; }
.bubble.md :deep(a),
.report-body.md :deep(a) { color: var(--primary); }
.bubble.md :deep(hr),
.report-body.md :deep(hr) { border: none; border-top: 1px solid var(--border); margin: 10px 0; }
.typing { color: var(--text-2); font-size: 13px; }

.error {
  margin: 0 20px 8px;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: #fdeceb;
  color: var(--danger);
  font-size: 13px;
}

/* 面试报告面板 */
.report {
  margin: 0 20px 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--panel);
  padding: 10px 12px;
}
.report-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.report-meta { margin-left: 8px; font-size: 12px; color: var(--text-2); }
.report-actions { display: flex; gap: 6px; }
.gaps { margin: 8px 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.gaps-label { font-size: 12px; color: var(--text-2); }
.gap-chip {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--primary-weak);
  color: var(--primary);
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.report-body {
  max-height: 40vh;
  overflow-y: auto;
  font-size: 13px;
  border-top: 1px solid var(--border);
  padding-top: 8px;
}
.report-body.md :deep(h1) { font-size: 16px; margin: 4px 0 8px; }
.report-body.md :deep(h2) { font-size: 14px; margin: 10px 0 6px; }
.report-body.md :deep(h3) { font-size: 13px; margin: 8px 0 4px; }
.report-body.md :deep(p),
.report-body.md :deep(ul) { margin: 4px 0; }
.report-body.md :deep(ul) { padding-left: 20px; }

.modes {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px 0;
  flex-wrap: wrap;
}
.modes .spacer { flex: 1; }
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
.sm { padding: 4px 10px; font-size: 12px; }

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
