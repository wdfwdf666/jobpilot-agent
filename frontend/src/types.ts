/** 与后端 app/schemas.py 对齐的类型定义 */

/** 检索命中的原文片段（回答的可追溯依据） */
export interface SourceRef {
  text: string
  source: string
  category: string
  distance: number
}

/** JD 分析：从 JD 原文抽取出的结构化要求 */
export interface JdAnalysis {
  position: string
  hard_requirements: string[]
  soft_requirements: string[]
  implicit_preferences: string[]
  keywords: string[]
}

/** 单条要求的匹配打分（evidence 为简历原文证据，找不到则为空） */
export interface MatchItem {
  requirement: string
  evidence: string
  score: number
  gap_advice: string
}

export interface MatchResult {
  overall_score: number
  items: MatchItem[]
}

/** 人工审核请求：后端 interrupt() 抛出的载荷，前端据此渲染审核卡片 */
export interface ReviewRequest {
  type: string
  position?: string
  hard_requirements?: string[]
  soft_requirements?: string[]
  match_result?: MatchResult
  hint?: string
}

/** 人工审核决定：确认，或按修正后的要求重算 */
export interface ReviewDecision {
  action: 'approve' | 'edit'
  hard_requirements?: string[]
  soft_requirements?: string[]
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: SourceRef[]
  /** JD 分析的结构化产物：匹配度面板直接消费，而不是让模型把它写进 markdown */
  matchResult?: MatchResult
  jdAnalysis?: JdAnalysis
  /** 待人工确认的审核请求；提交后置空并把决定记在 reviewDecision */
  review?: ReviewRequest
  reviewDecision?: ReviewDecision
  /** 本次匹配度是否经过人工修正（后端 artifacts.edited_by_human） */
  editedByHuman?: boolean
}

/** /chat/state 的返回：刷新页面后据此恢复上次的匹配度面板与待审核卡片 */
export interface ChatState {
  intent: string
  artifacts: SseArtifacts
  pending_interrupt: boolean
  interrupt?: ReviewRequest
  message_count: number
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

/** 批量上传：单个文件的处理结果 */
export interface UploadFileResult {
  filename: string
  ok: boolean
  added?: number
  skipped?: number
  error?: string
}

/** 批量上传：整批汇总 */
export interface UploadBatchResult {
  results: UploadFileResult[]
  ok_count: number
  fail_count: number
  added: number
  skipped: number
}

/** 文档管理：按 source+category 分组的一个文档 */
export interface KbDocument {
  source: string
  category: string
  chunks: number
}

/** 文档内的单个块 */
export interface KbChunk {
  id: string
  text: string
  metadata: {
    source?: string
    category?: string
    tags?: string
  }
}

/** 面试报告里回链到知识库的一个薄弱点 */
export interface KnowledgeGap {
  question: string
  /** 短标签（题目摘要），用作 chip 文案；query 会很长，不适合直接当按钮文字 */
  label?: string
  missing_points: string[]
  category: string
  /** 送去知识库检索的关键词 */
  query: string
  hits: { source: string; category: string; text: string }[]
}

export interface InterviewReport {
  session_id: string
  generated_at: string
  turns: number
  average_score: number
  markdown: string
  knowledge_gaps: KnowledgeGap[]
}

/** 左侧会话列表的一行（GET /chat/sessions） */
export interface SessionItem {
  session_id: string
  title: string
  /** 面试 | 对话（后端按 done 时的 intent 标） */
  kind: string
  turns: number
  created_at: string
  updated_at: string
}

/** SSE 事件负载 */
export interface SseDelta {
  text: string
}
export interface SseArtifacts {
  sources?: SourceRef[]
  match_result?: MatchResult
  jd_analysis?: JdAnalysis
  reviewed?: boolean
  edited_by_human?: boolean
  [key: string]: unknown
}
export interface SseDone {
  intent: string
  reply_chars?: number
}

/** 流式聊天回调 */
export interface StreamHandlers {
  onDelta: (text: string) => void
  onStatus?: (text: string) => void
  onArtifacts?: (artifacts: SseArtifacts) => void
  /** 图在 interrupt 处挂起，等待人工确认（human-in-the-loop） */
  onInterrupt?: (request: ReviewRequest) => void
  onDone?: (done: SseDone) => void
  onError: (message: string) => void
}
