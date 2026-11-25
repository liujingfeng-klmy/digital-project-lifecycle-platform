<script setup>
/**
 * 在办箱：待办箱里点开过、还没提交的件。
 *
 * 口径（2026-11-24）：点开即算在办 —— 点开就是要动手。存过草稿的带出
 * 上次填的内容，没存过的直接进去办。它和待办是两回事：待办是还没点开，
 * 这里是点开过没办完。混在一张页面上，待办就不像待办了。
 */
import { STAGE_STYLE } from '@/constants/stages'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { projectApi } from '@/api'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const router = useRouter()

const items = ref([])
const loading = ref(false)


// 管理员不参与办理，箱子里永远是空的 —— 空态要说清原因，别让人以为坏了
const emptyTip = computed(() => (userStore.role === 'admin'
  ? '管理员不参与办理，这里不会有件。'
  : '在待办箱点开一件，它就会移到这儿；办完提交自动移出。'))

async function load() {
  loading.value = true
  try {
    const { data } = await projectApi.inbox()
    items.value = data.doing?.items || []
  } finally {
    loading.value = false
  }
}

onMounted(load)

/* 点一行直接进详情并摊开那张表：在办箱的意义就是少找一步，
   进去还要从一排按钮里认出刚才点的是哪个，等于白存 */
function resume(item) {
  const q = { from: 'inbox' }
  if (item.draft?.action_key) {
    q.action = item.draft.action_key
    q.resume = '1'
  }
  router.push({ name: 'project-detail', params: { id: item.id }, query: q })
}

/** 保存时间：今天只显时:分，跨天带日期 */
function fmtSaved(s) {
  if (!s) return ''
  const d = new Date(s)
  const now = new Date()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')} ${hm}`
}
</script>

<template>
  <div class="inbox-page">
    <div class="card">
      <div class="card-head">
        <span class="card-title">在办箱</span>
        <span class="card-count">{{ items.length }}</span>
        <el-button link type="primary" class="head-right" @click="load">刷新</el-button>
      </div>

      <div v-loading="loading" class="card-body">
        <div v-if="!items.length" class="empty">
          <p>在办箱是空的。</p>
          <p class="empty-tip">{{ emptyTip }}</p>
        </div>

        <div v-else class="row-list">
          <div v-for="t in items" :key="t.id" class="row" role="button" tabindex="0"
               @click="resume(t)" @keydown.enter="resume(t)" @keydown.space.prevent="resume(t)">
            <div class="row-main">
              <div class="row-line">
                <span class="mono code">{{ t.code }}</span>
                <span class="stage-tag" :class="'st-' + (STAGE_STYLE[t.stage] || 'wait')">
                  {{ t.stage_label }}<template v-if="t.round">·{{ t.round }}</template>
                </span>
                <span class="row-kind">{{ t.draft?.action_label || '已打开，未保存' }}</span>
              </div>
              <div class="row-name">{{ t.name }}</div>
              <div class="row-sub">
                {{ t.unit }}
                <span v-if="t.investment"> · {{ Number(t.investment).toLocaleString('zh-CN') }} 万元</span>
                <span v-if="t.draft" class="saved"> · {{ fmtSaved(t.draft.updated) }} 存过一版</span>
                <span v-else class="saved"> · 点开过，可以直接接着办</span>
              </div>
            </div>
            <div class="row-act">
              <span class="row-action">接着办</span>
              <span class="arrow">→</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inbox-page { max-width: 1480px; }

.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--line-soft);
}
.card-title { font-size: 15px; font-weight: 600; }
.card-count {
  min-width: 20px; height: 19px; padding: 0 5px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 10px; background: var(--brand-bg); color: var(--brand-deep);
  font-size: 11.5px; font-weight: 600; font-variant-numeric: tabular-nums;
}
.head-right { margin-left: auto; }
.card-body { padding: 8px 20px 14px; }

.empty { padding: 26px 0 18px; color: var(--ink-3); font-size: 13px; text-align: center; }
.empty p { margin: 0; }
.empty-tip { font-size: 12px; color: var(--ink-4); margin-top: 4px; line-height: 1.7; }

.row-list { display: flex; flex-direction: column; }
.row {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 13px 4px; border-bottom: 1px solid var(--line-soft); cursor: pointer;
  transition: background 0.12s;
}
.row:last-child { border-bottom: 0; }
.row:hover { background: #F8FBFF; }
.row-main { min-width: 0; }
.row-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.code { font-size: 11.5px; color: var(--ink-4); letter-spacing: -0.02em; }
.row-kind { font-size: 11.5px; color: var(--brand-deep); }
.row-name { font-size: 14px; margin-top: 4px; color: var(--ink); line-height: 1.45; }
.row-sub { font-size: 12px; color: var(--ink-3); margin-top: 2px; }
.saved { color: var(--ink-4); }
.row-act { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.row-action { font-size: 12.5px; color: var(--brand); }
.arrow { color: var(--ink-4); font-size: 13px; transition: transform 0.15s; }
.row:hover .arrow { transform: translateX(2px); color: var(--brand); }
</style>
