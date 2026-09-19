<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { addDocument, fetchStats, searchKb, uploadDocumentsBatch } from '../api'
import type { KbHit, UploadFileResult } from '../types'
import KBManager from './KBManager.vue'

const props = defineProps<{ refreshKey: number }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const CATEGORIES = ['八股文', '项目笔记', '行业认知', '简历素材'] as const

const totalChunks = ref(0)
const tab = ref<'add' | 'upload' | 'manage' | 'search'>('add')

// 粘贴入库
const text = ref('')
const category = ref<string>('八股文')
const busy = ref(false)
const notice = ref('')
const noticeType = ref<'ok' | 'err'>('ok')

// 上传入库（支持多选，批量提交）
const fileInput = ref<HTMLInputElement | null>(null)
const fileResults = ref<UploadFileResult[]>([])

function goResumeUpload() {
  tab.value = 'upload'
  category.value = '简历素材'
}

// 检索测试
const query = ref('')
const hits = ref<KbHit[]>([])

async function refreshStats() {
  totalChunks.value = await fetchStats()
}

onMounted(refreshStats)
watch(() => props.refreshKey, refreshStats)

async function submitText() {
  if (!text.value.trim() || busy.value) return
  busy.value = true
  notice.value = ''
  try {
    const r = await addDocument({ text: text.value, category: category.value })
    show(`已入库 ${r.added} 块${r.skipped ? `，去重跳过 ${r.skipped} 块` : ''}`, 'ok')
    text.value = ''
    emit('changed')
  } catch (e) {
    show(e instanceof Error ? e.message : String(e), 'err')
  } finally {
    busy.value = false
  }
}

async function submitFile() {
  const files = Array.from(fileInput.value?.files ?? [])
  if (!files.length || busy.value) return
  busy.value = true
  notice.value = ''
  try {
    const r = await uploadDocumentsBatch(files, category.value)
    // 单文件失败不影响整批：汇总 + 逐文件明细都展示出来
    const okSummary = `成功 ${r.ok_count} 个、共入库 ${r.added} 块` +
      (r.skipped ? `（去重跳过 ${r.skipped} 块）` : '')
    if (r.fail_count) show(`批量上传：${okSummary}，失败 ${r.fail_count} 个（见明细）`, 'err')
    else show(`批量上传：${okSummary}`, 'ok')
    fileResults.value = r.results
    if (fileInput.value) fileInput.value.value = ''
    emit('changed')
  } catch (e) {
    show(e instanceof Error ? e.message : String(e), 'err')
  } finally {
    busy.value = false
  }
}

async function doSearch() {
  if (!query.value.trim()) return
  busy.value = true
  notice.value = ''
  try {
    hits.value = await searchKb(query.value)
  } catch (e) {
    show(e instanceof Error ? e.message : String(e), 'err')
  } finally {
    busy.value = false
  }
}

function show(msg: string, type: 'ok' | 'err') {
  notice.value = msg
  noticeType.value = type
  if (type === 'ok') refreshStats()
}
</script>

<template>
  <section class="kb card">
    <div class="head">
      <h3>知识库</h3>
      <span class="stat">共 {{ totalChunks }} 块</span>
    </div>

    <div class="tabs">
      <button
        v-for="t in ([['add', '粘贴入库'], ['upload', '上传文件'], ['manage', '文档管理'], ['search', '检索测试']] as const)"
        :key="t[0]"
        class="tab"
        :class="{ active: tab === t[0] }"
        @click="tab = t[0]"
      >{{ t[1] }}</button>
    </div>

    <div class="body">
      <button class="quick-resume" @click="goResumeUpload">
        <span class="qr-title">上传我的简历</span>
        <span class="qr-sub">选「简历素材」分类，自动按板块解析入库（教育 / 技能 / 项目…）</span>
      </button>

      <template v-if="tab === 'add'">
        <textarea
          v-model="text"
          rows="7"
          placeholder="粘贴一段八股文 / 项目笔记…（内容哈希去重，重复添加会自动跳过）"
        />
        <div class="row">
          <select v-model="category">
            <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
          </select>
          <button :disabled="busy || !text.trim()" @click="submitText">入库</button>
        </div>
      </template>

      <template v-else-if="tab === 'upload'">
        <input ref="fileInput" type="file" multiple accept=".md,.txt,.pdf,.docx,.html,.htm" />
        <p class="tip">可按住 Ctrl / Shift 多选文件批量上传；「简历素材」分类会自动按板块解析（教育/技能/项目…）。</p>
        <div class="row">
          <select v-model="category">
            <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
          </select>
          <button :disabled="busy" @click="submitFile">上传并入库</button>
        </div>
        <ul v-if="fileResults.length" class="file-results">
          <li v-for="r in fileResults" :key="r.filename" :class="r.ok ? 'ok' : 'err'">
            <span class="mark">{{ r.ok ? '✓' : '✗' }}</span>
            <span class="name">{{ r.filename }}</span>
            <span class="detail">{{ r.ok ? `入库 ${r.added} 块${r.skipped ? `，去重跳过 ${r.skipped}` : ''}` : r.error }}</span>
          </li>
        </ul>
      </template>

      <template v-else-if="tab === 'manage'">
        <KBManager :refresh-key="props.refreshKey" @changed="emit('changed')" />
      </template>

      <template v-else>
        <div class="row">
          <input
            v-model="query"
            placeholder="输入查询，如：GIL 会影响多核吗"
            @keydown.enter="doSearch"
          />
          <button :disabled="busy" @click="doSearch">检索</button>
        </div>
        <div v-for="h in hits" :key="h.id" class="hit">
          <div class="hit-meta">
            <span class="cat">{{ h.category }}</span>
            <span>{{ h.source }}</span>
            <span class="dist">距离 {{ h.distance.toFixed(3) }}</span>
          </div>
          <p class="hit-text">{{ h.text }}</p>
        </div>
        <p v-if="!hits.length" class="hint">检索结果会显示在这里，可先跑 check_rag.py 造点数据。</p>
      </template>

      <div v-if="notice" class="notice" :class="noticeType">{{ notice }}</div>
    </div>
  </section>
</template>

<style scoped>
.kb { display: flex; flex-direction: column; padding: 16px; overflow-y: auto; }
.head { display: flex; justify-content: space-between; align-items: baseline; }
.head h3 { margin: 0; }
.stat { font-size: 12px; color: var(--text-2); }

.tabs { display: flex; gap: 6px; margin: 12px 0; }
.tab {
  background: var(--bg);
  color: var(--text-2);
  padding: 6px 12px;
  font-size: 13px;
  border-radius: 8px;
}
.tab.active { background: var(--primary-weak); color: var(--primary); font-weight: 600; }

.body { display: flex; flex-direction: column; gap: 10px; }
.body textarea { resize: vertical; }
.row { display: flex; gap: 10px; }
.row input, .row select { flex: 1; min-width: 0; }

.quick-resume {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  text-align: left;
  padding: 12px 14px;
  background: var(--primary-weak);
  border: 1px dashed var(--primary);
  border-radius: var(--radius);
  cursor: pointer;
}
.quick-resume:hover { filter: brightness(0.97); }
.qr-title { font-size: 14px; font-weight: 700; color: var(--primary); }
.qr-sub { font-size: 12px; color: var(--text-2); }

.hit { border-top: 1px solid var(--border); padding-top: 8px; }
.hit-meta {
  display: flex;
  gap: 10px;
  font-size: 12px;
  color: var(--text-2);
  align-items: center;
}
.hit-meta .cat {
  background: var(--primary-weak);
  color: var(--primary);
  padding: 1px 8px;
  border-radius: 6px;
}
.hit-meta .dist { margin-left: auto; }
.hit-text { margin: 6px 0 10px; font-size: 13px; color: var(--text); }

.hint { color: var(--text-2); font-size: 13px; }
.tip { color: var(--text-2); font-size: 12px; margin: 0; }

.file-results {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}
.file-results li { display: flex; gap: 8px; align-items: baseline; min-width: 0; }
.file-results li.ok .mark { color: var(--ok); }
.file-results li.err .mark { color: var(--danger); }
.file-results .name { font-weight: 600; white-space: nowrap; }
.file-results .detail {
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-results li.err .detail { color: var(--danger); }
.notice { font-size: 13px; padding: 8px 10px; border-radius: var(--radius); }
.notice.ok { background: #e8f6ee; color: var(--ok); }
.notice.err { background: #fdeceb; color: var(--danger); }
</style>
