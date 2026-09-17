/** 与后端 app/schemas.py 对齐的类型定义 */

/** 检索命中的原文片段（回答的可追溯依据） */
export interface SourceRef {
  text: string
  source: string
  category: string
  distance: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: SourceRef[]
}

export interface KbHit {
  id: string
  text: string
  source: string
  category: string
  distance: number
}

export interface IngestResult {
  added: number
  skipped: number
  total_chunks: number
}

/** SSE 事件负载 */
export interface SseDelta {
  text: string
}
export interface SseArtifacts {
  sources?: SourceRef[]
  [key: string]: unknown
}
export interface SseDone {
  intent: string
}

/** 流式聊天回调 */
export interface StreamHandlers {
  onDelta: (text: string) => void
  onStatus?: (text: string) => void
  onArtifacts?: (artifacts: SseArtifacts) => void
  onDone?: (done: SseDone) => void
  onError: (message: string) => void
}
