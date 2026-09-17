<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { addDocument, fetchStats, searchKb, uploadDocument } from '../api'
import type { KbHit } from '../types'

const props = defineProps<{ refreshKey: number }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const CATEGORIES = ['八股文', '项目笔记', '行业认知', '简历素材'] as const

const totalChunks = ref(0)
const tab = ref<'add' | 'upload' | 'search'>('add')

// 粘贴入库
const text = ref('')
const category = ref<string>('八股文')
const busy = ref(false)
const notice = ref('')
const noticeType = ref<'ok' | 'err'>('ok')

// 上传入库
const fileInput = ref<HTMLInputElement | null>(null)

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
  const file = fileInput.value?.files?.[0]
  if (!file || busy.value) return
  busy.value = true
  notice.value = ''
  try {
    const r = await uploadDocument(file, category.value)
    show(`「${file.name}」已入库 ${r.added} 块${r.skipped ? `，去重跳过 ${r.skipped} 块` : ''}`, 'ok')
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
        v-for="t in ([['add', '粘贴入库'], ['upload', '上传文件'], ['search', '检索测试']] as const)"
        :key="t[0]"
        class="tab"
        :class="{ active: tab === t[0] }"
        @click="tab = t[0]"
      >{{ t[1] }}</button>
    </div>

    <div class="body">
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
        <input ref="fileInput" type="file" accept=".md,.txt,.pdf,.docx,.html,.htm" />
        <p class="tip">选择「简历素材」分类上传时会自动按板块解析（教育/技能/项目…），检索更准。</p>
        <div class="row">
          <select v-model="category">
            <option v-for="c in CATEGORIES" :key="c" :value="c">{{ c }}</option>
          </select>
          <button :disabled="busy" @click="submitFile">上传并入库</button>
        </div>
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
.notice { font-size: 13px; padding: 8px 10px; border-radius: var(--radius); }
.notice.ok { background: #e8f6ee; color: var(--ok); }
.notice.err { background: #fdeceb; color: var(--danger); }
</style>
