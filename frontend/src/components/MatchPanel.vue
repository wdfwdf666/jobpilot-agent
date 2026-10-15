<script setup lang="ts">
import { computed } from 'vue'
import type { JdAnalysis, MatchItem, MatchResult } from '../types'

const props = defineProps<{
  jdAnalysis?: JdAnalysis
  matchResult: MatchResult
  editedByHuman?: boolean
}>()

// 最弱的排前面：这块面板的用途是"告诉我该补什么"，不是"给我看分数"
const sorted = computed<MatchItem[]>(() =>
  [...(props.matchResult.items ?? [])].sort((a, b) => a.score - b.score),
)

const overall = computed(() => Math.max(0, Math.min(100, props.matchResult.overall_score ?? 0)))

/** 分数分档：80+ 稳、60-79 需补、<60 是缺口。用颜色分档而不是纯数字，一眼能看出问题。 */
function level(score: number): 'good' | 'warn' | 'bad' {
  if (score >= 80) return 'good'
  if (score >= 60) return 'warn'
  return 'bad'
}

// 环形进度：r=26 时周长约 163.36，用 stroke-dasharray 画弧
const RING_R = 26
const RING_C = 2 * Math.PI * RING_R
const ringOffset = computed(() => RING_C * (1 - overall.value / 100))
</script>

<template>
  <section class="match-panel" :class="level(overall)">
    <header class="mp-head">
      <div class="mp-title">
        <span class="mp-kicker">JD 匹配度分析</span>
        <h4>{{ jdAnalysis?.position || '未识别岗位' }}</h4>
        <span v-if="editedByHuman" class="mp-edited">已按人工修正重算</span>
      </div>
      <div class="mp-ring">
        <svg viewBox="0 0 64 64" width="64" height="64" aria-hidden="true">
          <circle class="ring-bg" cx="32" cy="32" :r="RING_R" />
          <circle
            class="ring-fg"
            cx="32"
            cy="32"
            :r="RING_R"
            :stroke-dasharray="RING_C"
            :stroke-dashoffset="ringOffset"
          />
        </svg>
        <div class="mp-score">
          <strong>{{ overall }}</strong>
          <span>/100</span>
        </div>
      </div>
    </header>

    <div v-if="jdAnalysis" class="mp-tags">
      <div v-for="group in [
        { label: '硬性要求', items: jdAnalysis.hard_requirements },
        { label: '加分项', items: jdAnalysis.soft_requirements },
        { label: '隐性偏好', items: jdAnalysis.implicit_preferences },
      ]" :key="group.label">
        <template v-if="group.items?.length">
          <span class="mp-label">{{ group.label }}</span>
          <span v-for="t in group.items" :key="t" class="mp-chip">{{ t }}</span>
        </template>
      </div>
    </div>

    <ul class="mp-items">
      <li v-for="(item, i) in sorted" :key="i" class="mp-item" :class="level(item.score)">
        <div class="mp-item-head">
          <span class="mp-req">{{ item.requirement }}</span>
          <span class="mp-val">{{ item.score }}</span>
        </div>
        <div class="mp-bar"><i :style="{ width: item.score + '%' }" /></div>
        <p v-if="item.evidence" class="mp-evidence">简历依据：{{ item.evidence }}</p>
        <p v-else class="mp-evidence none">简历中未找到对应证据</p>
        <p v-if="item.gap_advice" class="mp-advice">建议：{{ item.gap_advice }}</p>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.match-panel {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--panel);
  padding: 12px 14px;
  font-size: 13px;
}
.mp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.mp-kicker {
  font-size: 11px;
  letter-spacing: 0.4px;
  color: var(--text-2);
}
.mp-title h4 { margin: 2px 0 0; font-size: 15px; }
.mp-edited {
  display: inline-block;
  margin-top: 4px;
  font-size: 11px;
  padding: 0 6px;
  border-radius: 5px;
  background: var(--primary-weak);
  color: var(--primary);
}
.mp-ring { position: relative; flex: none; }
.mp-ring svg { transform: rotate(-90deg); }
.ring-bg { fill: none; stroke: var(--border); stroke-width: 6; }
.ring-fg {
  fill: none;
  stroke: var(--primary);
  stroke-width: 6;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.4s ease;
}
.match-panel.good .ring-fg { stroke: var(--ok); }
.match-panel.warn .ring-fg { stroke: #d98c00; }
.match-panel.bad .ring-fg { stroke: var(--danger); }
.mp-score {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 1px;
}
.mp-score strong { font-size: 17px; color: var(--text); }
.mp-score span { font-size: 10px; color: var(--text-2); }

.mp-tags { display: flex; flex-direction: column; gap: 4px; margin: 10px 0 4px; }
.mp-label {
  font-size: 11px;
  color: var(--text-2);
  margin-right: 6px;
}
.mp-chip {
  display: inline-block;
  font-size: 11px;
  padding: 1px 7px;
  margin: 2px 4px 2px 0;
  border-radius: 999px;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text-2);
}

.mp-items { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.mp-item { border-top: 1px solid var(--border); padding-top: 8px; }
.mp-item-head { display: flex; align-items: baseline; gap: 8px; }
.mp-req { flex: 1; min-width: 0; font-weight: 600; }
.mp-val { font-variant-numeric: tabular-nums; color: var(--text-2); }
.mp-item.good .mp-val { color: var(--ok); }
.mp-item.warn .mp-val { color: #d98c00; }
.mp-item.bad .mp-val { color: var(--danger); }

.mp-bar {
  height: 5px;
  border-radius: 3px;
  background: var(--bg);
  overflow: hidden;
  margin: 5px 0 6px;
}
.mp-bar i { display: block; height: 100%; border-radius: 3px; background: var(--primary); }
.mp-item.good .mp-bar i { background: var(--ok); }
.mp-item.warn .mp-bar i { background: #d98c00; }
.mp-item.bad .mp-bar i { background: var(--danger); }

.mp-evidence { margin: 0; color: var(--text-2); font-size: 12px; }
.mp-evidence.none { font-style: italic; }
.mp-advice { margin: 3px 0 0; font-size: 12px; color: var(--text); }
</style>
