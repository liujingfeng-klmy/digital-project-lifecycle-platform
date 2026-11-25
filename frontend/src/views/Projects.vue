<script setup>
import { STAGE_STYLE } from '@/constants/stages'
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { projectApi } from '@/api'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
// 侧栏「项目储备库」直接落到这一页，按储备阶段预筛。单独建路由而不是带
// query，是因为同一条导航要能稳定高亮、刷新后不回跳
const reserveView = computed(() => route.name === 'reserve')
// 窄窗口（≤1180）下这张表放不下八列：投资、查重、停留先收起，
// 横向滚动条比少看两列更烦人
const narrow = ref(false)
let mq = null
const syncNarrow = (e) => { narrow.value = e.matches }
onMounted(() => {
  mq = window.matchMedia('(max-width: 1180px)')
  narrow.value = mq.matches
  mq.addEventListener('change', syncNarrow)
})
onUnmounted(() => { if (mq) mq.removeEventListener('change', syncNarrow) })
const isApplicant = computed(() => userStore.role === 'applicant')

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const stats = ref(null)
const stages = ref([])
const filters = reactive({ q: '', stage: '', page: 1, size: 20 })

// 作用域：「我经办的」按流转动作留痕算，与「在办 / 已办结」不是一回事 ——
// 后者是项目终态，前者是「这个人经手过」。这一页叫「已办项目」，
// 默认落在自己经手过的件上；管理员要靠这一页兜底查全量，默认「全部项目」。
// 账号信息是登录后异步填的，初始化这一刻可能还没有角色 —— 补一个 watch，
// 角色到位后把默认值纠正过来，只在用户没手动切过时生效
const scope = ref(userStore.isAdmin ? 'all' : 'mine')
const scopeTouched = ref(false)
const mounted = ref(false)
const scopeParam = () => (scope.value === 'mine' ? 1 : undefined)

watch(() => userStore.user?.role, (role) => {
  if (!role || scopeTouched.value) return
  const want = role === 'admin' ? 'all' : 'mine'
  if (want === scope.value) return
  scope.value = want
  if (mounted.value) { filters.page = 1; loadList(); loadAux() }
}, { immediate: true })

function setScope(v) {
  scopeTouched.value = true
  if (scope.value === v) return
  scope.value = v
  filters.page = 1
  filters.stage = ''
  loadList()
  loadAux()
}


async function loadList() {
  loading.value = true
  try {
    const { data } = await projectApi.list({
      q: filters.q || undefined,
      stage: reserveView.value ? 'reserve' : (filters.stage || undefined),
      handled: scopeParam(),
      // 申报中还没提交的申报单不进台账：后端对 list/stats/stages 默认排除，
      // 这里不再单独传参 —— 少一个「某处忘了传」的坑
      page: filters.page,
      page_size: filters.size,
    })
    rows.value = data.results || []
    total.value = data.count || 0
  } finally {
    loading.value = false
  }
}

async function loadAux() {
  // 统计与阶段计数必须跟当前页同口径：储备库页只数储备库的件，
  // 否则顶上「项目总数 3」而表里只有 1 行，看着像数据没刷新
  const stage = reserveView.value ? 'reserve' : undefined
  const [s, st] = await Promise.all([
    projectApi.stats({ handled: scopeParam(), stage }),
    projectApi.stages({ handled: scopeParam(), stage }),
  ])
  stats.value = s.data
  stages.value = st.data.stages || []
}

// 申报人真正关心的是「还在办」和「已经完了」，不是十个阶段名。
// 后端 stage 参数支持逗号串，这里直接传一组。
// 不含申报中：这张表只列已经报出去的件，申报中的单子在待办箱
const RUNNING = 'precheck,supplement,expert_review,review_supplement,cost_review,cost_supplement,fiscal_review,approval,tendering,building,trial,acceptance'
const CLOSED = 'accepted,rejected,reserve'

const groupCount = (keys) => (stats.value?.by_stage || [])
  .filter((s) => keys.split(',').includes(s.key))
  .reduce((sum, s) => sum + s.count, 0)

function resetPage() {
  filters.page = 1
  loadList()
}

onMounted(() => {
  mounted.value = true
  if (reserveView.value) filters.stage = 'reserve'
  loadList()
  loadAux()
})

/* 「已办项目」和「项目储备库」是同一支路由组件（/projects 与 /reserve），
 * 在左侧栏来回点时组件被复用、不重新挂载 —— 不监听就会一直显示上次那个箱子的
 * 列表和数字（点储备库，表格里却是已办项目）。 */
watch(() => route.name, (name) => {
  const nowReserve = name === 'reserve'
  filters.page = 1
  filters.stage = nowReserve ? 'reserve' : ''
  loadList()
  loadAux()
})

/* 新建申报搬去待办工作台（components/NewProjectDialog.vue）。
 * 这一页叫「已办项目」，只列已经报出去的件；入口摆在这里，人要新建
 * 得先点进「已办」去找，方向是反的。 */

/**
 * 从这一页点进去是回看，不是办理：当前环节可能已经不在自己手上，
 * 把办理表单摆出来只会让人以为能办、其实按了没反应。
 * 申报人除外 —— 他在「已办项目」里还要补材料、交反馈
 */
function goDetail(row) {
  const from = reserveView.value ? 'reserve' : 'projects'
  router.push({
    name: 'project-detail',
    params: { id: row.id },
    query: isApplicant.value ? { from } : { from, view: 'readonly' },
  })
}

/**
 * 谁能删：管理员不限阶段；申报单位只能删自己名下的草稿。
 * 后端口径一致，这里只决定按钮露不露，真正的拦截在 ProjectViewSet.destroy。
 */
function canDelete(row) {
  if (userStore.role === 'admin') return true
  return userStore.role === 'applicant'
    && row.applicant === userStore.user?.id
    && row.stage === 'draft'
}

async function removeProject(row) {
  const isDraft = row.stage === 'draft'
  try {
    if (isDraft) {
      await ElMessageBox.confirm(
        `确认删除「${row.name}」？材料与记录一并删除，不可恢复。`,
        '删除项目',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
    } else {
      // 已进入流程的项目：二次确认到编号，避免点错行把在办项目清掉
      await ElMessageBox.prompt(
        `「${row.name}」当前阶段是「${row.stage_label}」。删除前系统会把流转留痕与材料原件归档到 data/deleted/，归档失败则不会删除。请输入项目编号 ${row.code} 确认。`,
        '删除已进入流程的项目',
        {
          type: 'warning',
          confirmButtonText: '确认删除',
          cancelButtonText: '取消',
          inputPlaceholder: row.code,
          inputValidator: (v) => (v || '').trim() === row.code || `请完整输入 ${row.code}`,
        },
      )
    }
  } catch { return }
  try {
    const { data } = await projectApi.remove(row.id)
    ElMessage.success(data?.detail || '已删除')
    if (rows.value.length === 1 && filters.page > 1) filters.page -= 1
    await loadList()
    loadAux()
  } catch { /* 错误提示由拦截器统一弹出 */ }
}

function dupText(row) {
  if (!row.dup_level) return '未查重'
  if (row.dup_level === 'attention') return `疑点 ${row.dup_attention}`
  if (row.dup_level === 'verify') return `待核实 ${row.dup_attention}`
  if (row.dup_level === 'none') return '未筛出'
  if (row.dup_level === 'skip') return '无正文'
  return '查重异常'
}

function fmtMoney(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
</script>

<template>
  <div class="proj">
    <div class="top-row">
      <div class="stat-strip">
        <div class="stat-tile">
          <div class="stat-num">{{ stats?.total ?? '—' }}</div>
          <div class="stat-label">项目总数</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ stats?.active ?? '—' }}</div>
          <div class="stat-label">在办</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ fmtMoney(stats?.investment_total) }}</div>
          <div class="stat-label">{{ reserveView ? '在库投资（万元）' : '累计投资（万元）' }}</div>
        </div>
        <div class="stat-tile" :class="{ alert: stats?.dup_flagged }">
          <div class="stat-num">{{ stats?.dup_flagged ?? '—' }}</div>
          <div class="stat-label">查重标记</div>
        </div>
      </div>
      <div class="top-actions">
        <!-- 新建申报在待办工作台：这一页叫「已办」，是回看已经报出去的件，
             不该摆建新单的入口 -->
        <el-button @click="loadList">刷新</el-button>
      </div>
    </div>

    <div class="toolbar">
      <!-- 只有管理员有「全部项目」这一档：审核岗的台账是自己经办过的件，
           别人的已办不是我的工作记录（后端也已收窄，这里不再给切换口）。
           非管理员留一行静态说明，免得看不出这张表是什么口径 -->
      <div v-if="userStore.isAdmin" class="segmented">
        <button class="seg" :class="{ on: scope === 'mine' }" @click="setScope('mine')">
          我经办的
        </button>
        <button class="seg" :class="{ on: scope === 'all' }" @click="setScope('all')">
          全部项目
        </button>
      </div>
      <div v-else-if="!isApplicant" class="scope-note">我经办的</div>
      <el-input
        v-model="filters.q"
        placeholder="搜项目名称、编号、单位"
        clearable
        class="search"
        @keyup.enter="resetPage"
        @clear="resetPage"
      >
        <template #prefix>
          <svg viewBox="0 0 24 24" width="14" height="14" class="search-icon" aria-hidden="true">
            <circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="1.7" />
            <path d="M16 16l4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
          </svg>
        </template>
      </el-input>
      <!-- 储备库页只有一个阶段，下拉不但没用，那一串「项目储备库（4）」的计数
           还容易被当成「库里有 4 个」——直接不显示 -->
      <el-select
        v-if="!reserveView"
        v-model="filters.stage"
        class="stage-select"
        placeholder="全部阶段"
        @change="resetPage"
      >
        <el-option label="全部阶段" :value="''" />
        <el-option :label="`在办（${groupCount(RUNNING)}）`" :value="RUNNING" />
        <el-option :label="`已办结（${groupCount(CLOSED)}）`" :value="CLOSED" />
        <!-- 计数为 0 的阶段不列：点进去必是空表，等于给了一条死路 -->
        <el-option
          v-for="s in stages.filter((x) => x.count > 0)"
          :key="s.key"
          :label="`${s.label}（${s.count}）`"
          :value="s.key"
        />
      </el-select>
      <el-button @click="resetPage">查询</el-button>
    </div>

    <div class="table-card">
    <el-table :data="rows" v-loading="loading" class="table" @row-click="goDetail">
      <el-table-column label="编号" width="104">
        <template #default="{ row }">
          <span class="mono code">{{ row.code }}</span>
        </template>
      </el-table-column>
      <el-table-column label="项目 / 单位" min-width="180">
        <template #default="{ row }">
          <div class="proj-name">
            {{ row.name }}
            <span v-if="row.sensitive" class="sens">涉敏</span>
          </div>
          <div class="proj-sub">{{ row.unit }} · {{ row.applicant_display }}</div>
        </template>
      </el-table-column>
      <!-- 领域只作辅助判断，窗口一窄就先让位 -->
      <el-table-column v-if="!narrow" label="领域" width="80">
        <template #default="{ row }">
          <span class="muted">{{ row.category || '—' }}</span>
        </template>
      </el-table-column>
      <!-- 投资 88：五位数带两位小数刚好一行，再宽就是白占地方 -->
      <el-table-column label="投资(万元)" width="88" align="right">
        <template #default="{ row }">
          <span class="mono">{{ fmtMoney(row.investment) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="阶段" width="104">
        <template #default="{ row }">
          <!-- 只写阶段名：轮次是环节内部的事，放这儿像在报第几遍进度 -->
          <span class="stage-tag" :class="'st-' + (STAGE_STYLE[row.stage] || 'wait')">
            {{ row.stage_label }}
          </span>
        </template>
      </el-table-column>
      <el-table-column v-if="!narrow" label="当前办理" width="86">
        <template #default="{ row }">
          <span class="muted">{{ row.handler_label
            || (['accepted', 'rejected', 'reserve'].includes(row.stage) ? '已结束' : '—') }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="!narrow" label="查重" width="78">
        <template #default="{ row }">
          <span
            class="dup"
            :class="{
              hot: row.dup_level === 'attention',
              warm: row.dup_level === 'verify',
            }"
          >{{ dupText(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="停留" width="58" align="right">
        <template #default="{ row }">
          <span v-if="['accepted', 'rejected', 'reserve'].includes(row.stage)" class="muted">—</span>
          <span v-else :class="['muted', { slow: row.days_in_stage >= 7 }]">
            {{ row.days_in_stage }} 天
          </span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="112" align="right">
        <template #default="{ row }">
          <el-button link type="primary" class="op" @click.stop="goDetail(row)">查看</el-button>
          <el-button
            v-if="canDelete(row)"
            link
            type="danger"
            class="op"
            @click.stop="removeProject(row)"
          >删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <div class="empty">
          <p v-if="scope === 'mine'">你还没有经办过项目。</p>
          <p v-else>还没有项目。</p>
          <p v-if="isApplicant" class="empty-tip">
            点右上「新建申报」，先传可研报告；没有材料也可以手工填。
          </p>
          <p v-else-if="scope === 'mine'" class="empty-tip">
            这里只列你经手过的件，切「全部项目」看全量。
          </p>
          <p v-else class="empty-tip">申报单位提交后，项目会出现在这里。</p>
        </div>
      </template>
    </el-table>

    <div class="table-foot">
      <span class="foot-count">共 {{ total }} 个项目</span>
      <el-pagination
        v-model:current-page="filters.page"
        :page-size="filters.size"
        :total="total"
        layout="prev, pager, next"
        @current-change="loadList"
      />
    </div>
    </div>

  </div>
</template>

<style scoped>
.proj { max-width: 1480px; }

.top-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.top-row .stat-strip { flex: 1; margin-bottom: 0; }
.top-actions { display: flex; align-items: center; gap: 12px; flex: 0 0 auto; padding-bottom: 2px; }

/* 工具条：分段控件 + 搜索 + 阶段下拉，一行装下，不再让十三个胶囊换行 */
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
}

.segmented {
  display: inline-flex;
  padding: 3px;
  border-radius: 9px;
  background: var(--panel-3);
  gap: 2px;
}
.seg {
  white-space: nowrap;
  padding: 5px 13px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--ink-3);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: 0.14s;
}
.seg:hover { color: var(--ink); }
.seg.on {
  background: #fff;
  color: var(--brand-deep);
  box-shadow: 0 1px 2px rgba(15, 31, 51, 0.08);
}

.search { width: 260px; }
/* 非管理员的台账口径说明：不是按钮，只是一句话，免得看着像被禁用的筛选 */
.scope-note {
  padding: 5px 13px;
  border-radius: 8px;
  background: var(--panel-3);
  color: var(--ink-2);
  font-size: 12.5px;
  white-space: nowrap;
}
.search-icon { color: var(--ink-4); }
.stage-select { width: 168px; }
.hint { font-size: 11.5px; color: var(--ink-4); margin-left: auto; }

.table-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}
.table { background: transparent; cursor: pointer; }
/* 编号是「XM2026-016」这种定长串，折成两行就认不出来了 */
.table .mono.code { white-space: nowrap; }
/* 查看 / 删除 两个字，别在窄列里竖着排 */
.table .op { white-space: nowrap; }
.table :deep(th.el-table__cell) { background: var(--panel-2); }
.table :deep(.el-table__row) { cursor: pointer; }

.table-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-top: 1px solid var(--line-soft);
  background: var(--panel-2);
}
.foot-count { font-size: 12px; color: var(--ink-3); }

.code { font-size: 11.5px; color: var(--ink-4); letter-spacing: -0.02em; }
.proj-name { font-size: 13.5px; color: var(--ink); line-height: 1.45; }
.sens {
  display: inline-block; margin-left: 6px; padding: 0 5px; border-radius: 4px;
  font-size: 12px; line-height: 16px; vertical-align: 1px;
  background: var(--danger-bg); color: var(--danger);
}
.proj-sub { font-size: 11.5px; color: var(--ink-4); margin-top: 2px; }
.muted { color: var(--ink-3); font-size: 12.5px; }
.muted.slow { color: var(--warn); }
.dup { font-size: 12px; color: var(--ink-4); }
.dup.hot { color: var(--danger); }
.dup.warm { color: var(--warn); }

.stage-tag {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 11.5px; font-weight: 500; height: 23px; line-height: 23px;
  padding: 0 8px; border-radius: 6px;
  background: var(--panel-3); color: var(--ink-3); white-space: nowrap;
}
.st-draft { background: var(--panel-3); color: var(--ink-3); }
.st-run { background: var(--brand-bg); color: var(--brand-deep); }
.st-back { background: var(--danger-bg); color: var(--danger); }
.st-expert { background: var(--purple-bg); color: var(--purple); }
.st-audit { background: var(--warn-bg); color: #9A4A0A; }
.st-wait { background: var(--panel-3); color: var(--ink-3); }
.st-done { background: var(--ok-bg); color: #05603A; }
.st-stop { background: var(--danger-bg); color: var(--danger); }

.empty { padding: 34px 0; color: var(--ink-3); font-size: 13px; }
.empty-tip { color: var(--ink-4); font-size: 12px; margin-top: 4px; line-height: 1.7; }

.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.tip-cell { font-size: 11.5px; color: var(--ink-4); line-height: 1.8; padding-top: 30px; }

</style>
