<script setup lang="ts">
import { ref, watch } from 'vue'
import type { ReviewDecision, ReviewRequest } from '../types'

const props = defineProps<{
  request: ReviewRequest
  decision?: ReviewDecision
  busy?: boolean
}>()

const emit = defineEmits<{ (e: 'submit', decision: ReviewDecision): void }>()

// 审核卡片是可编辑的：LLM 抽取难免有偏差（漏了年限、把加分项当硬性要求），
// 让人在图上"改一手"再继续，比事后在聊天里纠正划算得多。
const editing = ref(false)
const hardText = ref('')
const softText = ref('')

function resetFromRequest() {
  hardText.value = (props.request.hard_requirements ?? []).join('\n')
  softText.value = (props.request.soft_requirements ?? []).join('\n')
  editing.value = false
}
watch(() => props.request, resetFromRequest, { immediate: true })

function splitLines(text: string): string[] {
  return text.split('\n').map((s) => s.trim()).filter(Boolean)
}

function approve() {
  emit('submit', { action: 'approve' })
}

function submitEdit() {
  emit('submit', {
    action: 'edit',
    hard_requirements: splitLines(hardText.value),
    soft_requirements: splitLines(softText.value),
  })
}

const settled = () => props.decision !== undefined
</script>

<template>
  <section class="review" :class="{ done: settled() }">
    <header class="rv-head">
      <span class="rv-badge">{{ settled() ? '已处理' : '等待人工确认' }}</span>
      <strong>{{ request.position || '未识别岗位' }}</strong>
    </header>

    <p class="rv-hint">
      {{ request.hint || '确认抽取出的岗位要求后再继续生成结论。' }}
    </p>

    <template v-if="settled()">
      <p class="rv-done">
        {{ decision?.action === 'edit' ? '已按你的修正继续生成结论' : '已确认，继续生成结论' }}
      </p>
    </template>

    <template v-else-if="!editing">
      <div v-if="request.hard_requirements?.length" class="rv-group">
        <span class="rv-label">硬性要求</span>
        <span v-for="t in request.hard_requirements" :key="t" class="rv-chip">{{ t }}</span>
      </div>
      <div v-if="request.soft_requirements?.length" class="rv-group">
        <span class="rv-label">门槛/加分项</span>
        <span v-for="t in request.soft_requirements" :key="t" class="rv-chip soft">{{ t }}</span>
      </div>
      <div class="rv-actions">
        <button class="primary" :disabled="busy" @click="approve">
          {{ busy ? '处理中…' : '确认并继续' }}
        </button>
        <button class="ghost" :disabled="busy" @click="editing = true">修正要求</button>
      </div>
    </template>

    <template v-else>
      <label class="rv-field">
        <span class="rv-label">硬性要求（一行一条）</span>
        <textarea v-model="hardText" rows="4" />
      </label>
      <label class="rv-field">
        <span class="rv-label">门槛/加分项（一行一条）</span>
        <textarea v-model="softText" rows="3" />
      </label>
      <div class="rv-actions">
        <button class="primary" :disabled="busy" @click="submitEdit">
          {{ busy ? '处理中…' : '提交修正并继续' }}
        </button>
        <button class="ghost" :disabled="busy" @click="editing = false">取消</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.review {
  border: 1px dashed var(--primary);
  border-radius: var(--radius);
  background: var(--primary-weak);
  padding: 10px 12px;
  font-size: 13px;
}
.review.done {
  border-style: solid;
  border-color: var(--border);
  background: var(--panel);
}
.rv-head { display: flex; align-items: center; gap: 8px; }
.rv-badge {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--primary);
  color: #fff;
}
.review.done .rv-badge { background: var(--ok); }
.rv-hint { margin: 6px 0; color: var(--text-2); font-size: 12px; }
.rv-done { margin: 6px 0 0; color: var(--ok); font-size: 12px; }

.rv-group { margin: 6px 0; }
.rv-label {
  font-size: 11px;
  color: var(--text-2);
  margin-right: 6px;
}
.rv-chip {
  display: inline-block;
  font-size: 11px;
  padding: 1px 7px;
  margin: 2px 4px 2px 0;
  border-radius: 999px;
  background: var(--panel);
  border: 1px solid var(--border);
}
.rv-chip.soft { color: var(--text-2); }

.rv-actions { display: flex; gap: 8px; margin-top: 8px; }
.rv-field { display: block; margin: 6px 0; }
.rv-field textarea {
  width: 100%;
  margin-top: 3px;
  font-size: 12.5px;
  resize: vertical;
}
</style>
