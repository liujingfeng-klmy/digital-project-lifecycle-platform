<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { showError } from '@/api/http'
import { projectApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { fmtTime, ORDER } from '@/utils/projectFormat'
import { downloadDoc } from '@/utils/projectFiles'
import { collectDocOf, costReportsOf, costTasksOf, fmtSaved } from '@/utils/projectDetailModel'
import PrecheckDoc from './act/PrecheckDoc.vue'
import CostDoc from './act/CostDoc.vue'
import AcceptDoc from './act/AcceptDoc.vue'

/* 本环节办理：这一屏只回答「我现在该做什么」。
 * 承办与时限、要出的文书、按不动的门槛、动作按钮，都收在同一张卡里；
 * 三块文书面板各自成构件（预审修正说明 / 费用评审 / 验收材料），
 * 这样改哪一块都不用翻整页。 */
const props = defineProps({
  p: { type: Object, required: true },
  viewOnly: Boolean,
  actions: { type: Array, default: () => [] },
  actionBlockReason: { type: Function, default: () => '' },
  canUpload: Boolean,
  uploadLabel: { type: String, default: '上传申报材料' },
  fullArchive: Boolean,
})
const emit = defineEmits(['open-action', 'open-upload', 'open-reassign', 'open-raw',
  'refresh', 'resume-draft'])

const userStore = useUserStore()

/* 环节时限：管理员在「系统设置」里定，这里只读。
   超期不拦流程 —— 时限是管理指标，不是业务规则，拦了只会逼人补假日期。 */
const sla = computed(() => props.p?.sla || { days: 0, used: 0, remain: 0, overdue: 0 })
const slaTone = computed(() => {
  const s = sla.value
  if (!s.days) return 'plain'
  if (s.overdue > 0) return 'danger'
  if (s.remain <= 1) return 'warn'
  return 'ok'
})
// 进度条那一行前面已写了「时限 N 天 · 已用 N 天」，这里只补结论，不重复时限
const slaShort = computed(() => {
  const s = sla.value
  if (!s.days) return ''
  return s.overdue > 0 ? `超期 ${s.overdue} 天` : `剩余 ${s.remain} 天`
})

/* 存过草稿就明说，别让人以为自己没存上、又重填一遍。
   草稿本身由外壳的办理弹窗写，这里只读 p.my_draft。 */
const myDraft = computed(() => props.p?.my_draft || null)
const draftSavedAt = computed(() => fmtSaved(myDraft.value?.updated))
const resumeDraft = () => emit('resume-draft')

const openNotices = computed(() => (props.p?.notices || []).filter((n) => n.status === 'open'))
const openNotice = computed(() => openNotices.value[0] || null)

const compactStage = computed(() => props.p?.stage === 'approval')

/* 预审出修正说明、费用评审出任务书、立项出批复 —— 三个环节各一份对外文书 */
const DOC_STAGES = ['precheck', 'cost_review', 'approval']
const canGenDoc = computed(() => ['reviewer', 'auditor', 'admin'].includes(userStore.role))
const docStageVisible = computed(() => !!props.p && !props.viewOnly
  && DOC_STAGES.includes(props.p.stage) && canGenDoc.value
  // 这个环节一件都轮不到我办，就别把「生成立项批复」摆出来 —— 点了也是 403
  && props.actions.length > 0)
const docBusy = ref(false)

async function genApproval() {
  docBusy.value = true
  try {
    const { data } = await projectApi.genApproval(props.p.id, {})
    ElMessage.success(data.detail)
    await downloadDoc(props.p.id, data.doc)
    emit('refresh')
  } catch (e) {
    showError(e?.response?.data?.detail || '生成失败')
  } finally {
    docBusy.value = false
  }
}

/* 卡里的门槛提示要判断「缺哪份材料」，与面板用的是同一组筛选（见 model） */
const costTasks = computed(() => costTasksOf(props.p))
const costReports = computed(() => costReportsOf(props.p))
const collectDoc = computed(() => collectDocOf(props.p))

const askUpload = () => emit('open-upload')
const openAction = (a) => emit('open-action', a)
const openReassign = () => emit('open-reassign')
const emitRefresh = () => emit('refresh')
</script>

<template>
<div class="tab-cols">
  <div class="col-main">
<el-card v-if="!viewOnly" shadow="never" class="card act-card">
  <template #header>
    <div class="act-head">
      <span class="card-title">本环节办理</span>
      <span class="ah-stage">{{ p.stage_label }}</span>
      <!-- 有草稿就明说，别让人以为自己没存上、又重填一遍 -->
      <el-tooltip v-if="myDraft" placement="top" popper-class="stat-tip"
        :content="`${myDraft.action_label}：${draftSavedAt} 存过一版，点这里接着填`">
        <span class="draft-flag" @click="resumeDraft">在办 · 已存草稿</span>
      </el-tooltip>
      <span v-if="sla.days" class="sla-chip" :class="'sl-' + slaTone">
        {{ slaShort }}
      </span>
    </div>
  </template>

  <!-- 承办与时限收成一条窄带：原来是「头卡片签 + 一行数字 + 一根空进度条」
       三处说同一件事，还各占一行 -->
  <div class="act-meta">
    <div class="am-cell">
      <span class="am-k">承办</span>
      <span class="am-v">{{ p.handler_label || '已结束' }}</span>
      <el-button v-if="userStore.role === 'admin' && p.handler_role"
                 link type="primary" size="small"
                 style="margin-left:8px;padding:0"
                 @click="openReassign">改派</el-button>
    </div>
    <template v-if="sla.days">
      <div class="am-cell">
        <span class="am-k">时限</span>
        <span class="am-v">{{ sla.days }} 天</span>
      </div>
      <div class="am-cell">
        <span class="am-k">已用</span>
        <span class="am-v">{{ sla.used }} 天</span>
      </div>
      <div class="am-prog">
        <div class="sla-track">
          <div
            class="sla-fill"
            :class="'sl-' + slaTone"
            :style="{ width: Math.min(100, Math.round((sla.used / sla.days) * 100)) + '%' }"
          />
        </div>
        <span class="am-pct" :class="'sl-' + slaTone">
          {{ Math.min(100, Math.round((sla.used / sla.days) * 100)) }}%
        </span>
      </div>
    </template>
  </div>

  <div v-if="openNotice" class="notice-mini">
    <div class="notice-mini-title">
      有 {{ openNotice.items.length }} 项{{ openNotice.kind === 'audit'
        ? '待反馈' : openNotice.kind === 'review' ? '待修改' : '待补正' }}
    </div>
    <div class="notice-mini-sub">
      第 {{ openNotice.round }} 轮 · {{ openNotice.kind_label }}
      <template v-if="openNotice.deadline"> · 期限 {{ openNotice.deadline }}</template>
    </div>
  </div>

  <div v-if="docStageVisible" class="doc-panel">
    <div class="dp-head">
      <span class="dp-title">
        {{ p.stage === 'precheck' ? '预审修正说明'
          : p.stage === 'cost_review' ? '费用评审任务书' : '立项批复' }}
      </span>
      <span class="dp-sub">
        {{ p.stage === 'cost_review'
          ? (costTasks.length
            ? '已生成，下载盖章后送达；收到评估意见回来登记结论'
            : '填财评机构并生成，下载盖章后送达；送达后回来登记评估意见')
          : `按模板生成给 ${p.unit} 的批复；出具后才能转待招标` }}
      </span>
      <!-- 立项只有这一个动作，按钮直接摆在这一行，不再多垫一段说明 -->
      <el-button
        v-if="p.stage === 'approval'"
        class="dp-act"
        type="primary"
        :loading="docBusy"
        @click="genApproval"
      >生成立项批复</el-button>
    </div>

  <!-- 预审：勾选要写进函件的问题 -->
  <PrecheckDoc v-if="p.stage === 'precheck'" :p="p" @refresh="emitRefresh" />
  <!-- 费用评审：委托财评机构，默认 7 个自然日 -->
  <CostDoc v-else-if="p.stage === 'cost_review'" :p="p" :view-only="viewOnly"
    @refresh="emitRefresh" @open-raw="(row) => emit('open-raw', row)" />
  </div>



  <AcceptDoc v-if="['acceptance', 'accepted'].includes(p.stage)" :p="p"
    :view-only="viewOnly" @refresh="emitRefresh"
    @open-raw="(row) => emit('open-raw', row)" />

  <!-- 本环节办理顺序提示 -->
  <div v-if="p.stage === 'precheck'" class="act-tip">
    顺序：规则预审（查形式）→ AI 深度审查（查内容，需先配模型）→ 有硬性缺项就「退回补正」，
    没有就填初审意见「初审通过」。两者都只列问题，不下结论。
  </div>
  <div v-if="p.stage === 'cost_review'" class="act-tip">
    ① 生成《费用评审任务书》，盖章送达财评机构；② 送达后点「保存」，
    件留在在办箱等报告（默认 7 个自然日）；③ 报告回来后上传「第三方评估报告」；
    ④ 再点「费用评审通过」—— 结论由报告承担。
    「征求反馈」是把评估意见发单位确认，可多轮。
  </div>
  <div v-if="p.stage === 'cost_supplement'" class="act-tip">
    按「本轮需处理」逐条核对，材料点「上传补正材料」补。
    <b>无异议、也没有材料要补的，直接提交即可</b>，系统按「无异议」留痕。
  </div>
  <div v-if="p.stage === 'approval'" class="act-tip">
    办理顺序：① 生成立项批复并送达申报单位；② 点「批复已出具，转待招标」。
  </div>
  <div v-if="p.stage === 'trial'" class="act-tip">
    试运行由建设单位执行：填「试运行情况」与「数据归集情况」——
    两栏都有模板语句，点「填入示例」照着改数字即可。提交后进入待验收，
    我局据此出具《数据归集证明》；没有归集不能验收。
  </div>
  <div v-if="p.stage === 'acceptance'" class="act-tip">
    办理顺序：① 核对申报单位填报的试运行与归集情况；② 按模板出具
    《数据归集证明》并送建设单位；③ 确认验收、项目结项。
    专家验收意见不必现在传 —— 结项后在「已验收」里补传备案。
  </div>

  <div v-if="actions.some((a) => a.key === 'cost_review_pass') && !costReports.length"
    class="act-block-tip">
    还没有评估报告，「费用评审通过」暂不可用 —— <b>先在本环节「第三方评估报告」里上传</b>。
  </div>
  <div v-if="actions.some((a) => a.key === 'accept') && !collectDoc"
    class="act-block-tip">
    还没有出具《数据归集证明》，<b>不能验收</b> —— 先在上方「数据归集证明」里
    按模板出具并送建设单位；数据未归集的项目不能结项。
  </div>

  <div v-if="actions.length || canUpload" class="act-foot">
    <span class="af-label">{{ actions.length ? '办理动作' : '材料准备' }}</span>
    <div class="af-btns">
      <!-- 补材料就是这一环的活，入口摆在动作按钮同一行，不再藏到页面右上角 -->
      <el-button v-if="canUpload" class="act-btn" @click="askUpload()">
        {{ uploadLabel }}
      </el-button>
      <el-button
        v-for="a in actions"
        :key="a.key"
        :type="actionBlockReason(a) ? 'default'
          : (a.primary === false ? 'default' : 'primary')"
        class="act-btn"
        :disabled="!!actionBlockReason(a)"
        :title="actionBlockReason(a) || undefined"
        @click="openAction(a)"
      >{{ a.label }}</el-button>
      <span v-if="actions.some((a) => actionBlockReason(a))" class="act-block-tip">
        灰掉的按钮：{{ actions.filter((a) => actionBlockReason(a)).map((a) => `${a.label}（${actionBlockReason(a)}）`).join('；') }}
      </span>
    </div>
  </div>
  <div v-if="!actions.length && !canUpload" class="act-notice">
    当前阶段没有你可执行的动作。{{ p.handler_label ? `等${p.handler_label}办理。` : '流程已结束。' }}
  </div>          </el-card>

  <!-- 立项只出批复：说明白为什么下面空了，免得以为资料丢了或页面坏了 -->
  <div v-if="compactStage && !fullArchive" class="quiet-note">
    立项环节只出批复；过程结论在「审核结论」页签。
  </div>

  <!-- 本轮要处理的事：办理时最需要看的东西，摆在按钮上面。
       注意它在办理卡之外——原版面就是这样，挪进卡里会多套一层内边距。 -->
  <el-card v-if="openNotices.length" shadow="never" class="card">
    <template #header>
      <div class="card-head">
        <span class="card-title">本轮需处理</span>
        <span class="card-sub">
          {{ openNotices.length }} 项未办结<template
            v-if="openNotices[0].deadline">，期限 {{ openNotices[0].deadline }}</template>
        </span>
      </div>
    </template>
    <div v-for="n in openNotices" :key="n.id" class="notice">
      <div class="notice-head">
        <span class="notice-round">第 {{ n.round }} 轮</span>
        <span class="notice-kind" :class="n.kind">{{ n.kind_label }}</span>
        <span class="muted">{{ n.issued_by_name }} · {{ fmtTime(n.issued_at) }}</span>
      </div>
      <ol class="notice-items">
        <li v-for="(it, i) in n.items" :key="i">
          <b>{{ it.title }}</b>
          <span v-if="it.requirement">：{{ it.requirement }}</span>
        </li>
      </ol>
      <div v-if="n.note" class="notice-note">说明：{{ n.note }}</div>
    </div>
  </el-card>
  </div>
</div>
</template>

<style scoped>
@import 'base.css';

.col-main {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}


.notice-mini {
  margin-top: 10px;
  border-left: 2px solid var(--danger);
  padding: 5px 10px;
  background: var(--danger-bg);
  border-radius: 0 6px 6px 0;
}

.notice-mini-title {
  font-size: 12px;
  color: var(--danger);
}

.notice-mini-sub {
  font-size: 12px;
  color: var(--ink-3);
}

/* ------------------------------------------------------------ 本环节办理置顶卡
   办事的入口、时限、要出的文书都收在这一张卡里，打开详情第一眼就能办。
   版式自上而下分五段：标题条（阶段 + 剩余）→ 承办时限窄带 → 文书面板
   → 办理顺序 → 动作条，段与段之间用留白和细线分开，不再靠一串虚线往下堆 */
.act-card {
  border-color: var(--brand-line);
}

.act-card :deep(.el-card__header) {
  background: var(--panel);
  border-bottom: 1px solid var(--line-soft);
  padding: 11px 16px;
}

.act-card :deep(.el-card__body) {
  padding: 16px 18px 18px;
}

.act-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.act-head .card-title {
  position: relative;
  padding-left: 10px;
}

/* 标题前一道色条：比整条淡蓝底更轻，也不至于跟右栏卡片撞色 */
.act-head .card-title::before {
  content: "";
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 13px;
  border-radius: 2px;
  background: var(--brand);
}

.draft-flag {
  font-size: 12px;
  line-height: 17px;
  padding: 0 8px;
  border-radius: 4px;
  color: #8a5a00;
  background: var(--warn-bg);
  border: 1px solid #f3ddc2;
  cursor: pointer;
  white-space: nowrap;
}

.draft-flag:hover {
  background: #fbeed9;
}

.ah-stage {
  font-size: 12px;
  line-height: 17px;
  padding: 0 7px;
  border-radius: 4px;
  color: var(--brand-deep);
  background: var(--brand-bg);
  border: 1px solid var(--brand-line);
}


/* 承办与时限：收成一条窄带，值比标签重，进度条吃掉剩下的宽度 */
.act-meta {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
  padding: 9px 12px;
  border-radius: 9px;
  background: var(--panel-2);
  border: 1px solid var(--line-soft);
}

.am-cell {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.am-k {
  font-size: 12px;
  color: var(--ink-4);
}

.am-v {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ink);
}

.am-prog {
  flex: 1;
  min-width: 110px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.am-prog .sla-track {
  flex: 1;
}

.am-pct {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}


.sla-chip {
  margin-left: auto;
  font-size: 11.5px;
  padding: 2px 9px;
  border-radius: 20px;
  border: 1px solid var(--line);
  background: var(--panel-2);
  white-space: nowrap;
}

.sl-ok {
  color: var(--ok);
}

.sl-warn {
  color: var(--warn);
}

.sl-danger {
  color: var(--danger);
}

.sl-plain {
  color: var(--ink-3);
}

.sla-chip.sl-ok {
  background: var(--ok-bg);
  border-color: #bce5d2;
}

.sla-chip.sl-warn {
  background: var(--warn-bg);
  border-color: #f3ddc2;
}

.sla-chip.sl-danger {
  background: var(--danger-bg);
  border-color: #f1c6c2;
}


.sla-track {
  height: 4px;
  border-radius: 2px;
  background: var(--bg-deep);
  overflow: hidden;
}

.sla-fill {
  height: 100%;
  background: var(--brand);
  transition: width 0.3s;
}

.sla-fill.sl-ok {
  background: var(--ok);
}

.sla-fill.sl-warn {
  background: var(--warn);
}

.sla-fill.sl-danger {
  background: var(--danger);
}


/* 文书：独立面板，跟表单、动作分开 */
.doc-panel {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid var(--line-soft);
  border-radius: 10px;
  background: var(--panel-2);
}

.dp-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.dp-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
}

.dp-sub {
  font-size: 11.5px;
  color: var(--ink-4);
}

.dp-act {
  margin-left: auto;
}


/* 办理顺序：浅底一条，放在动作上方，先看怎么走再点按钮 */
/* 浅底会跟上面的文书面板连成一片「一叠盒子」，这里只用一道左侧线 */
.act-tip {
  margin-top: 16px;
  padding: 4px 0 4px 11px;
  border-left: 2px solid var(--brand-line);
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.8;
}

.act-tip b {
  color: var(--ink-2);
  font-weight: 500;
}


.act-foot {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  /* 按钮行是卡片的最后一行：底下留 6px，加上页面留白，按钮离卡片边线
     仍有二十来像素，不再压在框线上 */
  margin-top: 22px;
  padding: 18px 0 6px;
  border-top: 1px solid var(--line-soft);
}

.af-label {
  font-size: 12px;
  color: var(--ink-4);
  letter-spacing: 0.04em;
}

.af-btns {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.act-btn {
  margin-left: 0 !important;
  min-width: 104px;
}

.act-notice {
  margin-top: 22px;
  padding: 18px 0 6px;
  border-top: 1px solid var(--line-soft);
  font-size: 11.5px;
  color: var(--ink-4);
  line-height: 1.8;
}


/* 立项环节折叠上游内容后的说明：一行小字，不用虚线框 */
.quiet-note {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 2px 2px 0;
  font-size: 12px;
  color: var(--ink-4);
  line-height: 1.85;
}


.act-block-tip {
  margin-top: 14px;
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.7;
}
</style>
