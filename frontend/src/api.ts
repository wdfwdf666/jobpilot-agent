/**
 * 后端 API 封装。
 *
 * 面试考点：后端聊天接口是 POST + SSE。浏览器原生 EventSource 只支持 GET、
 * 不能携带 body，因此用 fetch + ReadableStream 手动解析 SSE 帧
 * （帧格式：`event: <name>\ndata: <json>\n\n`）。
 */
import type {
  ChatMessage,
  IngestResult,
  KbChunk,
  KbDocument,
  KbHit,
  StreamHandlers,
  UploadBatchResult,
} from './types'

const BASE = '/api'

// ---------- 聊天 ----------

export async function streamChat(
  sessionId: string,
  message: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  mode = 'auto',
): Promise<void> {
  const resp = await fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message, mode }),
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
    case 'done':
      handlers.onDone?.(payload as { intent: string })
      break
    case 'error':
      handlers.onError((payload as { message: string }).message)
      break
  }
}

export async function fetchHistory(sessionId: string): Promise<ChatMessage[]> {
  const resp = await fetch(`${BASE}/chat/history/${sessionId}`)
  if (!resp.ok) return []
  const data = (await resp.json()) as { messages: ChatMessage[] }
  return data.messages
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
