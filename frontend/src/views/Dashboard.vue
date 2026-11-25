<script setup>
/**
 * 待办工作台：只回答一件事 —— 今天该我动手的有哪几件。
 *
 * 在办箱（点开过、还没办完的件）单独一页，从左侧进；全局在办数、别人的件
 * 都不在这页出现 —— 那些数在「统计分析」里看，放在这里只添乱。
 */
import { STAGE_STYLE } from '@/constants/stages'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { projectApi } from '@/api'
import { useUserStore } from '@/stores/user'
import NewProjectDialog from '@/components/NewProjectDialog.vue'

const userStore = useUserStore()
const router = useRouter()

const inbox = ref({ todos: { count: 0, items: [] } })
const loading = ref(false)

/* 新建申报的入口放在这里，不放在已办项目 —— 那一页叫「已办」，
   右上角却摆着「新建」，人得先点进已办项目才能建一张新单。 */
const createShow = ref(false)

const isApplicant = computed(() => userStore.role === 'applicant')
const todos = computed(() => inbox.value.todos?.items || [])

// 空态按角色说清「为什么空、接着去哪」，不用写一段通用说明
const emptyTip = computed(() => {
  if (isApplicant.value) return '新建的申报单会出现在这里，提交后才进左侧「已办项目」。'
  if (userStore.role === 'admin') return '管理员不参与流转，可在「已办项目」查看全部项目。'
  return '别人推进一步后，这里自动出现。'
})

/* 建完单：留在工作台（同一页刷新，新件立刻排在待办里），
   或已由对话框跳去详情页传可研 */
function onCreated() {
  load()
}


async function load() {
  loading.value = true
  try {
    const { data } = await projectApi.inbox()
    inbox.value = data
  } finally {
    loading.value = false
  }
}

onMounted(load)

/* 直奔材料：专家评审要读可研，不该先翻概况和上千字建设内容。
   直接落到详情页的「项目资料」页签 —— 材料本来就属于这个件，
   原来跳到跨项目的资料页，等于先把人从件里拽出去 */
function go(item) {
  router.push({ name: 'project-detail', params: { id: item.id }, query: { from: 'todo' } })
}

function goFiles(item) {
  router.push({ name: 'project-detail', params: { id: item.id }, query: { tab: 'files', from: 'todo' } })
}

function todoTitle(item) {
  if (userStore.role === 'applicant') {
    if (item.stage === 'draft') return '待提交申报'
    if (item.stage === 'supplement') return `待补正 ${item.notice?.items?.length || 0} 项`
    if (item.stage === 'review_supplement') {
      return `按评审意见改材料（${item.notice?.items?.length || 0} 项）`
    }
    if (item.stage === 'cost_supplement') {
      return `待反馈评估意见（${item.notice?.items?.length || 0} 项）`
    }
    if (item.stage === 'trial') return '填试运行情况，申请验收'
  }
  if (userStore.role === 'reviewer') {
    if (item.stage === 'precheck') return '待预审'
    if (item.stage === 'expert_review') return '待录入评审会意见'
    if (item.stage === 'approval') return '待出具立项批复'
    return '待推进建设阶段'
  }
  if (userStore.role === 'auditor') {
    if (item.stage === 'cost_review') return '待费用评审'
    if (item.stage === 'fiscal_review') return '待财政审核'
    return '待验收确认'
  }
  return item.stage_label
}
</script>

<template>
  <div class="dash">
    <div class="card">
      <div class="card-head">
        <span class="card-title">待办</span>
        <span class="card-count">{{ todos.length }}</span>
        <div class="head-right">
          <el-button v-if="isApplicant" type="primary" size="small" @click="createShow = true">
            新建申报
          </el-button>
          <el-button link type="primary" @click="load">刷新</el-button>
        </div>
      </div>

      <div v-loading="loading" class="card-body">
        <div v-if="!todos.length" class="empty">
          <div class="empty-mark">✓</div>
          <p>没有待办件。</p>
          <p class="empty-tip">{{ emptyTip }}</p>
        </div>

        <div v-else class="todo-list">
          <div v-for="t in todos" :key="t.id" class="todo" role="button" tabindex="0"
               @click="go(t)" @keydown.enter="go(t)" @keydown.space.prevent="go(t)">
            <div class="todo-main">
              <div class="todo-line">
                <span class="mono code">{{ t.code }}</span>
                <span class="stage-tag" :class="'st-' + (STAGE_STYLE[t.stage] || 'wait')">
                  {{ t.stage_label }}<template v-if="t.round">·{{ t.round }}</template>
                </span>
                <span class="todo-kind">{{ todoTitle(t) }}</span>
              </div>
              <div class="todo-name">{{ t.name }}</div>
              <div class="todo-sub">
                {{ t.unit }}
                <span v-if="t.investment"> · {{ Number(t.investment).toLocaleString('zh-CN') }} 万元</span>
                <span v-if="t.days_in_stage" class="days"> · 已停留 {{ t.days_in_stage }} 天</span>
              </div>
              <div v-if="t.notice" class="todo-notice">
                补正事项：{{ (t.notice.items || []).map((i) => i.title).join('；') }}
              </div>
            </div>
            <div class="todo-act">
              <el-button
                v-if="t.files"
                link
                type="primary"
                size="small"
                class="todo-files"
                @click.stop="goFiles(t)"
              >可研 {{ t.files }}</el-button>
              <span class="todo-action">去办理</span>
              <span class="arrow">→</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <NewProjectDialog v-model="createShow" @created="onCreated" />
  </div>
</template>

<style scoped>
.dash { max-width: 1480px; }

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
.head-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.card-body { padding: 8px 20px 14px; }

.empty { padding: 26px 0 18px; color: var(--ink-3); font-size: 13px; text-align: center; }
.empty-mark {
  width: 34px; height: 34px; margin: 0 auto 10px;
  border-radius: 50%;
  background: var(--panel-2);
  border: 1px solid var(--line-soft);
  color: var(--ok);
  display: flex; align-items: center; justify-content: center;
  font-size: 15px;
}
.empty p { margin: 0; }
.empty-tip { font-size: 12px; color: var(--ink-4); margin-top: 4px; line-height: 1.7; }

.todo-list { display: flex; flex-direction: column; }
.todo {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 13px 4px; border-bottom: 1px solid var(--line-soft); cursor: pointer;
  transition: background 0.12s;
}
.todo:last-child { border-bottom: 0; }
.todo:hover { background: #F8FBFF; }
.todo-main { min-width: 0; }
.todo-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.code { font-size: 11.5px; color: var(--ink-4); letter-spacing: -0.02em; }
.todo-kind { font-size: 11.5px; color: var(--danger); }
.todo-name { font-size: 14px; margin-top: 4px; color: var(--ink); line-height: 1.45; }
.todo-sub { font-size: 12px; color: var(--ink-3); margin-top: 2px; }
.days { color: var(--warn); }
.todo-notice { font-size: 12px; color: var(--ink-2); margin-top: 5px; line-height: 1.65; }
.todo-act { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.todo-action { font-size: 12.5px; color: var(--brand); }
.todo-files { font-size: 11.5px; }
.arrow { color: var(--ink-4); font-size: 13px; transition: transform 0.15s; }
.todo:hover .arrow { transform: translateX(2px); color: var(--brand); }
</style>
