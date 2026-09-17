<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { deleteChunk, deleteDocument, fetchChunks, fetchDocuments, updateChunk } from '../api'
import type { KbChunk, KbDocument } from '../types'

const props = defineProps<{ refreshKey: number }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const docs = ref<KbDocument[]>([])
const expanded = ref<string | null>(null)
const chunks = ref<KbChunk[]>([])
const loadingDocs = ref(false)
const loadingChunks = ref(false)
const notice = ref('')
const noticeType = ref<'ok' | 'err'>('ok')

// 块编辑状态：正在编辑的块 id -> 草稿文本
const editingId = ref<string | null>(null)
const editDraft = ref('')

function docKey(d: KbDocument): string {
  return `${d.category}::${d.source}`
}

function show(msg: string, type: 'ok' | 'err') {
  notice.value = msg
  noticeType.value = type
}

async function refresh() {
  loadingDocs.value = true
  notice.value = ''
  try {
    docs.value = await fetchDocuments()
  } catch (e) {
    show(e instanceof Error ? e.message : String(e), 'err')
  } finally {
    loadingDocs.value = false
  }
}

onMounted(refresh)
watch(() => props.refreshKey, refresh)

async function toggleDoc(d: KbDocument) {
  const key = docKey(d)
  if (expanded.value === key) {
    expanded.value = null
    chunks.value = []
    return
  }
  expanded.value = key
  loadingChunks.value = true
  notice.value = ''
  try {
    chunks.value = await fetchChunks(d.source, d.category)
  } catch (e) {
    show(e instanceof Error ? e.message : String(e), 'err')
  } finally {
    loadingChunks.value = false
  }
}

async function removeDoc(d: KbDocument) {
  const ok = window.confirm(
    `确定删除文档「${d.source}」（${d.category}，共 ${d.chunks} 块）？\n删除后需重新上传/入库才能恢复。`
  )
  if (!ok) return
  try {
    const removed = await deleteDocument(d.source, d.category)
    show(`已删除 ${removed} 块`, 'ok')
    if (expanded.value === docKey(d)) {
      expanded.value = null
      chunks.value = []
    }
    emit('changed')
    await refresh()
  } catch (e) {
    show(e instanceof Error ? e.message : String(e), 'err')
  }
}

function startEdit(c: KbChunk) {
  editingId.value = c.id
  editDraft.value = c.text
}

function cancelEdit() {
  editingId.value = null
  editDraft.value = ''
}

async function saveEdit(c: KbChunk) {
  const text = editDraft.value.trim()
  if (!text || text === c.text) {
    cancelEdit()
    return
  }
  try {
    await updateChunk(c.id, text)
    show('已修改（已重新向量化）', 'ok')
    cancelEdit()
    emit('changed')
    await refresh() // 修改后块 id 会变，重新拉取
    if (expanded.value) {
      const [category, source] = expanded.value.split('::')
      chunks.value = await fetchChunks(source, category)
    }
  } catch (e) {
    show(e instanceof Error ? e.message : String(e), 'err')
  }
}

async function removeChunk(c: KbChunk) {
  if (!window.confirm('确定删除这一块？')) return
  try {
    await deleteChunk(c.id)
    chunks.value = chunks.value.filter((x) => x.id !== c.id)
    show('已删除该块', 'ok')
    emit('changed')
    await refresh()
  } catch (e) {
    show(e instanceof Error ? e.message : String(e), 'err')
  }
}
</script>

<template>
  <div class="manager">
    <div v-if="notice" class="notice" :class="noticeType">{{ notice }}</div>

    <p v-if="!loadingDocs && !docs.length" class="hint">
      库里还没有内容。先在上方「粘贴入库」或「上传文件」添加一些知识。
    </p>

    <div v-for="d in docs" :key="docKey(d)" class="doc">
      <div class="doc-row" :class="{ open: expanded === docKey(d) }">
        <button class="expand" @click="toggleDoc(d)">
          <span class="arrow">{{ expanded === docKey(d) ? '▾' : '▸' }}</span>
          <span class="cat">{{ d.category }}</span>
          <span class="name" :title="d.source">{{ d.source }}</span>
          <span class="count">{{ d.chunks }} 块</span>
        </button>
        <button class="del" title="删除整个文档" @click="removeDoc(d)">删除</button>
      </div>

      <div v-if="expanded === docKey(d)" class="chunks">
        <p v-if="loadingChunks" class="hint">加载中…</p>
        <div v-for="c in chunks" :key="c.id" class="chunk">
          <div v-if="editingId === c.id" class="edit-area">
            <textarea v-model="editDraft" rows="4" />
            <div class="edit-actions">
              <button class="primary" @click="saveEdit(c)">保存</button>
              <button @click="cancelEdit">取消</button>
            </div>
          </div>
          <template v-else>
            <p class="chunk-text">{{ c.text }}</p>
            <div class="chunk-actions">
              <span v-if="c.metadata.tags" class="tags">{{ c.metadata.tags }}</span>
              <span class="spacer" />
              <button class="mini" @click="startEdit(c)">修改</button>
              <button class="mini danger" @click="removeChunk(c)">删除</button>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.manager { display: flex; flex-direction: column; gap: 8px; }
.hint { color: var(--text-2); font-size: 13px; }
.notice { font-size: 13px; padding: 8px 10px; border-radius: var(--radius); }
.notice.ok { background: #e8f6ee; color: var(--ok); }
.notice.err { background: #fdeceb; color: var(--danger); }

.doc { border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.doc-row { display: flex; align-items: stretch; }
.doc-row + .chunks { border-top: 1px solid var(--border); }

.expand {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  background: transparent;
  padding: 8px 10px;
  text-align: left;
}
.expand:hover { background: var(--bg); }
.arrow { color: var(--text-2); font-size: 12px; width: 10px; }
.cat {
  background: var(--primary-weak);
  color: var(--primary);
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 6px;
  white-space: nowrap;
}
.name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.count { font-size: 12px; color: var(--text-2); white-space: nowrap; }
.del {
  font-size: 12px;
  color: var(--danger);
  background: transparent;
  padding: 0 12px;
}
.del:hover { background: #fdeceb; }

.chunks { display: flex; flex-direction: column; background: var(--bg); }
.chunk { padding: 8px 12px; border-top: 1px solid var(--border); }
.chunk:first-child { border-top: none; }
.chunk-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.chunk-actions { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.chunk-actions .spacer { flex: 1; }
.tags { font-size: 11px; color: var(--text-2); }
.mini { font-size: 12px; padding: 2px 10px; background: var(--panel); color: var(--text-2); }
.mini:hover { color: var(--primary); }
.mini.danger:hover { color: var(--danger); }

.edit-area { display: flex; flex-direction: column; gap: 8px; }
.edit-area textarea { font-size: 13px; }
.edit-actions { display: flex; gap: 8px; }
.primary { background: var(--primary); color: #fff; }
</style>
