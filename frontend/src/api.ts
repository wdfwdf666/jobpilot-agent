/**
 * 后端 API 封装。
 *
 * 面试考点：后端聊天接口是 POST + SSE。浏览器原生 EventSource 只支持 GET、
 * 不能携带 body，因此用 fetch + ReadableStream 手动解析 SSE 帧
 * （帧格式：`event: <name>\ndata: <json>\n\n`）。
 */
import type {
  ChatMessage,
  ChatState,
  IngestResult,
  InterviewReport,
  KbChunk,
  KbDocument,
  KbHit,
  ReviewDecision,
  StreamHandlers,
  UploadBatchResult,
} from './types'

// 接口基址两个环境各一个值：
// - 开发：'/api'，交给 vite.config.ts 的 proxy 转发到 127.0.0.1:8000；
// - 生产（单容器）：'/'，前端由后端 StaticFiles 同源托管，不必再套反向代理。
// 用 ?? 而不是 ||：空字符串是合法取值（同源根路径），|| 会把它误吞成 '/api'。
const BASE: string = import.meta.env.VITE_API_BASE ?? '/api'

// ---------- 聊天 ----------

/**
 * POST + SSE 的通用实现：/chat/stream 与 /chat/resume 只有请求体不同，
 * 帧解析与事件分发完全相同，抽出来避免两份实现漂移。
 */
async function postSse(
  url: string,
  body: unknown,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok || !resp.body) {
    handlers.onError(`请求失败：HTTP ${resp.status}`)
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE 帧以空行分隔
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      dispatchFrame(frame, handlers)
    }
  }
}

export async function streamChat(
  sessionId: string,
  message: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  mode = 'auto',
): Promise<void> {
  return postSse(
    `${BASE}/chat/stream`,
    { session_id: sessionId, message, mode },
    handlers,
    signal,
  )
}

/**
 * 恢复被人工审核挂起的一轮：后端用 Command(resume=decision) 驱动同一条线程，
 * 前端把返回的增量续写到同一条气泡里，所以体验上是"图暂停了一下又继续"。
 */
export async function resumeChat(
  sessionId: string,
  decision: ReviewDecision,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  return postSse(
    `${BASE}/chat/resume`,
    { session_id: sessionId, decision },
    handlers,
    signal,
  )
}

function dispatchFrame(frame: string, handlers: StreamHandlers): void {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7).trim()
    else if (line.startsWith('data: ')) data += line.slice(6)
  }
  if (!data) return
  let payload: unknown
  try {
    payload = JSON.parse(data)
  } catch {
    return
  }
  switch (event) {
    case 'delta':
      handlers.onDelta((payload as { text: string }).text)
      break
    case 'status':
      handlers.onStatus?.((payload as { text: string }).text)
      break
    case 'artifacts':
      handlers.onArtifacts?.(payload as Record<string, unknown>)
      break
    case 'interrupt':
      // 图在人工审核处挂起：本轮 SSE 到此为止（后端不会发 done）
      handlers.onInterrupt?.(payload as never)
      break
    case 'done':
      handlers.onDone?.(payload as { intent: string })
      break
    case 'error':
      handlers.onError((payload as { message: string }).message)
      break
  }
}

export async function fetchHistory(sessionId: string): Promise<ChatMessage[]> {
  const resp = await fetch(`${BASE}/chat/history/${encodeURIComponent(sessionId)}`)
  if (!resp.ok) return []
  const data = (await resp.json()) as { messages: ChatMessage[] }
  return data.messages
}

/** 拉取本场面试的评分报告（markdown + 可跳转的知识库回链） */
export async function fetchInterviewReport(sessionId: string): Promise<InterviewReport> {
  const resp = await fetch(`${BASE}/interview/report/${encodeURIComponent(sessionId)}`)
  if (!resp.ok) throw new Error(`获取面试报告失败：HTTP ${resp.status}`)
  return (await resp.json()) as InterviewReport
}

/**
 * 读检查点里的图状态：刷新页面后用它恢复"上次的匹配度面板"与"待人工确认的卡片"。
 * 检查点让图状态跨请求、跨进程存活，前端因此不必自己缓存这些结构化产物。
 */
export async function fetchChatState(sessionId: string): Promise<ChatState> {
  const resp = await fetch(`${BASE}/chat/state/${encodeURIComponent(sessionId)}`)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return (await resp.json()) as ChatState
}

export async function endInterview(sessionId: string): Promise<void> {
  await fetch(`${BASE}/chat/interview/end/${encodeURIComponent(sessionId)}`, { method: 'POST' })
}

// ---------- 知识库 ----------

export async function addDocument(req: {
  text: string
  source?: string
  category: string
}): Promise<IngestResult> {
  const resp = await fetch(`${BASE}/kb/documents`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: req.text, source: req.source ?? 'manual', category: req.category }),
  })
  if (!resp.ok) {
    const detail = ((await resp.json()) as { detail?: string }).detail ?? resp.status
    throw new Error(`入库失败：${detail}`)
  }
  return (await resp.json()) as IngestResult
}

export async function uploadDocument(file: File, category: string): Promise<IngestResult> {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch(`${BASE}/kb/upload?category=${encodeURIComponent(category)}`, {
    method: 'POST',
    body: form,
  })
  if (!resp.ok) {
    const detail = ((await resp.json()) as { detail?: string }).detail ?? resp.status
    throw new Error(`上传失败：${detail}`)
  }
  return (await resp.json()) as IngestResult
}

/** 批量上传：一次请求带全部文件，后端逐个处理、单文件失败不影响整批 */
export async function uploadDocumentsBatch(
  files: File[],
  category: string,
): Promise<UploadBatchResult> {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  const resp = await fetch(`${BASE}/kb/upload-batch?category=${encodeURIComponent(category)}`, {
    method: 'POST',
    body: form,
  })
  if (!resp.ok) {
    const detail = ((await resp.json()) as { detail?: string }).detail ?? resp.status
    throw new Error(`批量上传失败：${detail}`)
  }
  return (await resp.json()) as UploadBatchResult
}

export async function searchKb(query: string, topK = 5): Promise<KbHit[]> {
  const resp = await fetch(`${BASE}/kb/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_k: topK }),
  })
  if (!resp.ok) throw new Error(`检索失败：HTTP ${resp.status}`)
  const data = (await resp.json()) as { hits: KbHit[] }
  return data.hits
}

export async function fetchStats(): Promise<number> {
  const resp = await fetch(`${BASE}/kb/stats`)
  if (!resp.ok) return 0
  const data = (await resp.json()) as { total_chunks: number }
  return data.total_chunks
}

// ---------- 知识库文档管理 ----------

export async function fetchDocuments(): Promise<KbDocument[]> {
  const resp = await fetch(`${BASE}/kb/documents`)
  if (!resp.ok) throw new Error('获取文档列表失败')
  const data = (await resp.json()) as { documents: KbDocument[] }
  return data.documents
}

export async function fetchChunks(source: string, category: string): Promise<KbChunk[]> {
  const params = new URLSearchParams({ source, category })
  const resp = await fetch(`${BASE}/kb/chunks?${params}`)
  if (!resp.ok) throw new Error('获取文档内容失败')
  const data = (await resp.json()) as { chunks: KbChunk[] }
  return data.chunks
}

export async function deleteDocument(source: string, category: string): Promise<number> {
  const params = new URLSearchParams({ source, category })
  const resp = await fetch(`${BASE}/kb/documents?${params}`, { method: 'DELETE' })
  if (!resp.ok) {
    const detail = ((await resp.json()) as { detail?: string }).detail ?? resp.status
    throw new Error(`删除失败：${detail}`)
  }
  const data = (await resp.json()) as { removed: number }
  return data.removed
}

export async function deleteChunk(chunkId: string): Promise<void> {
  const resp = await fetch(`${BASE}/kb/chunks/${chunkId}`, { method: 'DELETE' })
  if (!resp.ok) {
    const detail = ((await resp.json()) as { detail?: string }).detail ?? resp.status
    throw new Error(`删除失败：${detail}`)
  }
}

export async function updateChunk(chunkId: string, text: string): Promise<void> {
  const resp = await fetch(`${BASE}/kb/chunks/${chunkId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!resp.ok) {
    const detail = ((await resp.json()) as { detail?: string }).detail ?? resp.status
    throw new Error(`修改失败：${detail}`)
  }
}
