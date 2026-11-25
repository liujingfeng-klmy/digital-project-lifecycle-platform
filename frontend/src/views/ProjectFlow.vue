<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { projectApi } from '@/api'

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const error = ref('')
const p = ref(null)
const filter = ref('all')

const STAGE_LABEL = {
  draft: '申报中',
  precheck: '预审中',
  supplement: '待补正',
  expert_review: '评审中',
  review_supplement: '评审后补正',
  cost_review: '费用评审',
  cost_supplement: '征求反馈',
  fiscal_review: '财政审核',
  approval: '立项',
  tendering: '待招标',
  building: '建设中',
  trial: '试运行',
  acceptance: '待验收',
  accepted: '已验收',
  reserve: '项目储备库',
  rejected: '不予通过',
}

const stageLabel = (k) => STAGE_LABEL[k] || k || ''

// 后端按时间倒序返回，这里翻成正序：从上往下读，与纸质卷宗一致
const events = computed(() => [...(p.value?.events || [])])

const isMove = (e) => !!(e.to_stage && e.to_stage !== e.from_stage)
const hasComment = (e) => !!(e.comment || '').trim()

const list = computed(() => {
  const all = events.value
  if (filter.value === 'move') return all.filter(isMove)
  if (filter.value === 'comment') return all.filter(hasComment)
  return all
})

const moveCount = computed(() => events.value.filter(isMove).length)
const commentCount = computed(() => events.value.filter(hasComment).length)
const actorCount = computed(() => new Set(
  events.value.map((e) => e.actor_name).filter(Boolean),
).size)

const spanDays = computed(() => {
  if (!events.value.length) return null
  const first = new Date(events.value[0].created).getTime()
  const last = new Date(events.value[events.value.length - 1].created).getTime()
  if (!Number.isFinite(first) || !Number.isFinite(last)) return null
  return Math.max(1, Math.round((last - first) / 86400000))
})

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'move', label: '阶段流转' },
  { key: 'comment', label: '经办意见' },
]

function dayOf(v) {
  return v ? String(v).slice(0, 10) : ''
}

function fmtTime(v) {
  return v ? String(v).replace('T', ' ').slice(0, 16) : '—'
}

function fmtDay(v) {
  const d = dayOf(v)
  if (!d) return ''
  const [y, m, dd] = d.split('-')
  return `${y} 年 ${Number(m)} 月 ${Number(dd)} 日`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await projectApi.detail(route.params.id)
    p.value = data
  } catch (err) {
    error.value = err?.response?.status === 404
      ? '项目不存在，可能已被删除'
      : '流转记录加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function goDetail() {
  // from 一路带着走：从待办点进来的件，看完留痕回办理页仍是待办，
  // 侧栏不会跳到「已办项目」去
  const q = {}
  if (route.query.from) q.from = route.query.from
  if (route.query.view) q.view = route.query.view
  router.push({ name: 'project-detail', params: { id: route.params.id }, query: q })
}

onMounted(load)
</script>

<template>
  <div class="page flow-page" v-loading="loading">
    <div v-if="error" class="load-error">
      <el-empty :description="error" :image-size="72" />
    </div>

    <template v-else-if="p">
      <section class="flow-head">
        <div class="fh-main">
          <div class="fh-id">
            <span class="fh-code mono">{{ p.code }}</span>
            <span class="fh-stage">{{ p.stage_label }}</span>
          </div>
          <h1 class="fh-title">{{ p.name }}</h1>
          <div class="fh-meta">
            <span>{{ p.unit }}</span>
            <i class="hm-sep" />
            <span>{{ p.year || '—' }} 年度</span>
            <i class="hm-sep" />
            <span>当前办理 {{ p.handler_label || '已结束' }}</span>
          </div>
        </div>
        <el-button @click="goDetail">回到办理页</el-button>
      </section>

      <div class="stat-strip">
        <div class="stat-tile">
          <div class="stat-num">{{ events.length }}<span class="stat-unit">条</span></div>
          <div class="stat-label">全部留痕</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ moveCount }}<span class="stat-unit">次</span></div>
          <div class="stat-label">阶段流转</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ actorCount }}<span class="stat-unit">人</span></div>
          <div class="stat-label">经办人</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ spanDays ?? '—' }}<span class="stat-unit">天</span></div>
          <div class="stat-label">首末动作跨度</div>
        </div>
      </div>

      <div class="filter-bar">
        <button
          v-for="f in FILTERS"
          :key="f.key"
          class="filter-chip"
          :class="{ on: filter === f.key }"
          @click="filter = f.key"
        >
          {{ f.label }}
          <span v-if="f.key === 'comment' && commentCount" class="chip-num">{{ commentCount }}</span>
        </button>
        <span class="filter-tip">按时间正序</span>
      </div>

      <div v-if="!list.length" class="flow-empty">
        {{ events.length ? '当前筛选条件下没有记录。' : '还没有流转记录。' }}
      </div>

      <ol v-else class="flow-list">
        <template v-for="(e, i) in list" :key="e.id">
          <li v-if="i === 0 || dayOf(e.created) !== dayOf(list[i - 1].created)" class="day-sep">
            <span>{{ fmtDay(e.created) }}</span>
          </li>
          <li class="flow-item" :class="{ move: isMove(e) }">
            <span class="fi-dot" :class="isMove(e) ? 'dot-move' : 'dot-act'" />
            <div class="fi-body">
              <div class="fi-top">
                <span class="fi-action">{{ e.action_label }}</span>
                <span v-if="isMove(e)" class="fi-move">
                  {{ e.from_stage ? stageLabel(e.from_stage) : '起始' }}
                  <b>→</b>
                  {{ stageLabel(e.to_stage) }}
                </span>
                <span class="fi-time">{{ fmtTime(e.created) }}</span>
              </div>
              <div class="fi-actor">{{ e.actor_name || '系统' }} · {{ e.actor_role_label }}</div>
              <div v-if="e.comment" class="fi-comment">{{ e.comment }}</div>
            </div>
          </li>
        </template>
      </ol>
    </template>
  </div>
</template>

<style scoped>
.flow-page { max-width: 980px; }
.load-error { padding: 72px 0; }

.flow-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 20px;
  padding: 20px 24px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-left: 3px solid var(--brand);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
}
.fh-id { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.fh-code { font-size: 11.5px; color: var(--ink-4); }
.fh-stage {
  font-size: 11.5px; font-weight: 500; height: 23px; line-height: 23px; padding: 0 8px; border-radius: 6px;
  background: var(--brand-bg); color: var(--brand-deep); border: 1px solid var(--brand-line);
}
.fh-title { font-size: 20px; font-weight: 600; line-height: 1.5; margin: 8px 0 6px; letter-spacing: -0.3px; }
.fh-meta {
  display: flex; align-items: center; gap: 9px; flex-wrap: wrap;
  font-size: 12.5px; color: var(--ink-3);
}
.hm-sep { width: 1px; height: 11px; background: var(--line-strong); }

.filter-bar { display: flex; align-items: center; gap: 8px; margin: 4px 0 16px; }
.filter-chip {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid var(--line); background: #fff; border-radius: 8px;
  padding: 4px 12px; cursor: pointer;
  font-family: inherit; font-size: 12.5px; color: var(--ink-2);
  transition: all 0.15s;
}
.filter-chip:hover { border-color: var(--brand-line); color: var(--brand-deep); }
.filter-chip.on {
  background: var(--brand-bg); border-color: var(--brand-line);
  color: var(--brand-deep); font-weight: 500;
}
.chip-num {
  font-family: var(--font-mono); font-size: 12px;
  background: #fff; border-radius: 5px; padding: 0 6px;
}
.filter-tip { font-size: 11.5px; color: var(--ink-4); margin-left: auto; }

.flow-empty {
  padding: 48px 0; text-align: center;
  font-size: 12.5px; color: var(--ink-4);
  background: var(--panel); border: 1px dashed var(--line); border-radius: var(--radius-lg);
}

.flow-list { list-style: none; margin: 0; padding: 0; }
.day-sep {
  position: relative;
  padding: 6px 0 8px 26px;
  font-size: 12px; color: var(--ink-4); letter-spacing: 0.06em;
}
.day-sep::before {
  content: ""; position: absolute; left: 5px; top: 14px;
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--panel); border: 1px solid var(--line-strong);
}
.flow-item {
  position: relative;
  padding: 0 0 14px 26px;
  border-left: 1px solid var(--line-soft);
  margin-left: 8px;
}
.flow-item:last-child { border-left-color: transparent; padding-bottom: 4px; }
.fi-dot {
  position: absolute; left: -4.5px; top: 5px;
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--brand-line); border: 1.5px solid #fff;
  box-shadow: 0 0 0 1px var(--line-strong);
}
.dot-move { background: var(--brand); box-shadow: 0 0 0 1px var(--brand); }
.fi-body {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 11px 14px;
  box-shadow: 0 1px 2px rgba(28, 27, 24, 0.03);
}
.flow-item.move .fi-body { border-left: 2px solid var(--brand-line); }
.fi-top { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.fi-action { font-size: 13px; color: var(--ink); font-weight: 500; }
.fi-move { font-size: 11.5px; color: var(--ink-3); }
.fi-move b { color: var(--ink-4); font-weight: 400; margin: 0 2px; }
.fi-time { margin-left: auto; font-size: 12px; color: var(--ink-4); font-family: var(--font-mono); }
.fi-actor { font-size: 11.5px; color: var(--ink-3); margin-top: 3px; }
.fi-comment {
  font-size: 12px; color: var(--ink-2); line-height: 1.8;
  margin-top: 7px; padding-top: 7px;
  border-top: 1px dashed var(--line-soft);
  white-space: pre-wrap;
}
</style>
