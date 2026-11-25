<script setup>
import { computed } from 'vue'
import { fmtTime } from '@/utils/projectFormat'
import { conclusionModel } from '@/utils/projectDetailModel'
import { viewReviewFile as viewReviewFileApi, viewScan as viewScanApi } from '@/utils/projectFiles'
import PrecheckReport from './concl/PrecheckReport.vue'
import DupCheck from './concl/DupCheck.vue'

/* 审核结论：只放「我这一步凭什么做判断」。
 * 逐环节显式映射上游结论（见 model 里的 UPSTREAM_KEEP），没映射到的环节
 * 就没有结论要核对、这一页签不出现；回放全过程的活归「流转记录」，
 * 两边不重复摊同一堆东西。 */
const props = defineProps({
  p: { type: Object, required: true },
  fullArchive: Boolean,
})
const emit = defineEmits(['refresh'])

const model = computed(() => conclusionModel(props.p, { fullArchive: props.fullArchive }))

const archived = computed(() => model.value.archived)
const upstreamHidden = computed(() => model.value.upstreamHidden)
const votes = computed(() => model.value.votes)
const voteSummary = computed(() => model.value.voteSummary)
const reviewStageSeen = computed(() => model.value.reviewStageSeen)
const costRounds = computed(() => model.value.costRounds)
const costReturnCount = computed(() => model.value.costReturnCount)
const showCostRounds = computed(() => model.value.showCostRounds)
const conclNotices = computed(() => model.value.conclNotices)
const showPrecheckCards = computed(() => model.value.showPrecheckCards)
const showDupCards = computed(() => model.value.showDupCards)
const showReviewMeeting = computed(() => model.value.showReviewMeeting)
const conclusionRows = computed(() => model.value.conclusionRows)
const conclusionTitle = computed(() => model.value.conclusionTitle)

/* 评审会材料：各专家意见表与汇总表分开存，一份一条记录。
   一场会收上来七八张纸，拼成一个 PDF 就等于没法按人核对。 */
const reviewFiles = computed(() => props.p?.review_files || [])
const reviewFileCounts = computed(() => {
  const c = { expert: 0, summary: 0, other: 0 }
  for (const f of reviewFiles.value) c[f.kind] = (c[f.kind] || 0) + 1
  return c
})

const viewReviewFile = (f) => viewReviewFileApi(props.p.id, f)
const viewScan = () => viewScanApi(props.p.id, props.p.review_scan)
const emitRefresh = () => emit('refresh')
</script>

<template>
<div class="tab-pane">
  <!-- ② 前置审核意见：上游环节已经定下的事，本环节办理时核对。
       征求反馈之后只留本轮反馈事项与下游要用的结论，审批过程收进完整档案。
       已验收是归档态：过程结论不再摊开，要看走「流转记录」 -->
  <div v-if="!archived && (conclusionRows.length
    || conclNotices.length
    || (showReviewMeeting && reviewStageSeen) || showCostRounds)" class="zone">
    <!-- 费用评审轮次：只列征求反馈、单位反馈与结论，多轮也看得清 -->
    <el-card v-if="showCostRounds" shadow="never" class="card">
      <template #header>
        <div class="card-head">
          <span class="card-title">费用评审轮次</span>
          <span class="card-sub">
            共 {{ costReturnCount }} 次征求反馈 · 当前第 {{ costReturnCount + 1 }} 轮
          </span>
        </div>
      </template>
      <div class="cf-list">
        <div v-for="r in costRounds" :key="r.id" class="cf-row">
          <div class="cf-round">第 {{ r.round }} 轮</div>
          <div class="cf-body">
            <div class="cf-head">
              <span class="cf-act" :class="'ca-' + r.action">{{ r.action_label }}</span>
              <span class="cf-actor">{{ r.actor_name || '系统' }}</span>
              <span class="cf-time">{{ fmtTime(r.created) }}</span>
            </div>
            <ol v-if="r.items.length" class="cf-items">
              <li v-for="(it, i) in r.items" :key="i">
                <b>{{ it.title }}</b><span v-if="it.requirement">：{{ it.requirement }}</span>
              </li>
            </ol>
            <div v-if="r.comment" class="cf-note">{{ r.comment }}</div>
          </div>
        </div>
      </div>
    </el-card>

    <!-- 补正 / 本轮反馈事项 -->
    <el-card v-if="conclNotices.length" shadow="never" class="card">
      <template #header>
        <div class="card-head">
          <span class="card-title">补正与反馈记录</span>
          <span class="card-sub">{{ conclNotices.length }} 轮已办结</span>
        </div>
      </template>
      <div v-for="n in conclNotices" :key="n.id" class="notice">
        <div class="notice-head">
          <span class="notice-round">第 {{ n.round }} 轮</span>
          <span class="notice-kind" :class="n.kind">{{ n.kind_label }}</span>
          <span class="notice-status" :class="n.status">{{ n.status_label }}</span>
          <span class="muted">{{ n.issued_by_name }} · {{ fmtTime(n.issued_at) }}</span>
          <span v-if="n.deadline" class="muted">期限 {{ n.deadline }}</span>
        </div>
        <ol class="notice-items">
          <li v-for="(it, i) in n.items" :key="i">
            <b>{{ it.title }}</b>
            <span v-if="it.requirement">：{{ it.requirement }}</span>
          </li>
        </ol>
        <div v-if="n.note" class="notice-note">说明：{{ n.note }}</div>
        <div v-if="n.reply" class="notice-reply">补正回复：{{ n.reply }}</div>
      </div>
    </el-card>

    <!-- 评审会结论：只在评审期与评审后补正出现，费用评审起不再展示 -->
    <el-card v-if="showReviewMeeting && reviewStageSeen" shadow="never" class="card">
      <template #header>
        <div class="card-head">
          <span class="card-title">评审会结论</span>
          <span class="card-sub">
            <template v-if="votes.recorded">{{ voteSummary }}</template>
            <template v-else>
              评审会结束后，由初审岗上传签字扫描件并录入票数
            </template>
          </span>
        </div>
      </template>
      <div v-if="votes.recorded" class="vote-grid">
        <div class="vote-box">
          <div class="vote-num">{{ votes.agree }}</div>
          <div class="vote-label">通过</div>
        </div>
        <div class="vote-box">
          <div class="vote-num">{{ votes.modified }}</div>
          <div class="vote-label">修改后通过</div>
        </div>
        <div class="vote-box">
          <div class="vote-num">{{ votes.reject }}</div>
          <div class="vote-label">不通过</div>
        </div>
        <div class="vote-box vote-sum">
          <div class="vote-num">{{ votes.yes }}/{{ votes.total }}</div>
          <div class="vote-label">通过票数（过半即通过）</div>
        </div>
      </div>
      <div v-else class="muted" style="padding: 6px 0">
        还没有录入评审会票数。
      </div>

      <div class="expert-line">
        <div class="review-files-head">
          <span>评审会材料</span>
          <span class="muted">
            专家意见表 {{ reviewFileCounts.expert }} 份 ·
            汇总意见表 {{ reviewFileCounts.summary }} 份
          </span>
        </div>
        <div v-if="reviewFiles.length" class="review-files">
          <div v-for="f in reviewFiles" :key="f.id" class="review-file">
            <span class="rf-kind" :class="f.kind">{{ f.kind_label }}</span>
            <el-button link type="primary" size="small" class="rf-name"
              @click="viewReviewFile(f)">{{ f.name }}</el-button>
            <span class="muted">{{ f.uploaded_by_name }}</span>
          </div>
        </div>
        <template v-else-if="p.review_scan">
          <el-button link type="primary" size="small" @click="viewScan">
            {{ p.review_scan.name }}
          </el-button>
          <span class="muted">（改造前上传的单份扫描件）</span>
        </template>
        <span v-else class="muted">还没有上传评审会材料。</span>
      </div>

      <el-alert
        v-if="p.stage === 'review_supplement'"
        type="info" :closable="false" show-icon class="act-alert"
        title="评审会结论为「修改后通过」：等申报单位按意见修改材料，改完交费用评审"
      />
    </el-card>

    <!-- 环节结论：只列本环节需要核对的上游结论，一行一条 -->
    <el-card v-if="conclusionRows.length" shadow="never" class="card">
      <template #header>
        <div class="card-head">
          <span class="card-title">{{ conclusionTitle }}</span>
        </div>
      </template>
      <div class="concl-list">
        <div v-for="r in conclusionRows" :key="r.key" class="concl-row">
          <div class="concl-label">{{ r.label }}</div>
          <div class="concl-main">
            <div class="concl-line">
              <span class="concl-val" :class="{ mono: r.mono }">{{ r.val }}</span>
              <span class="concl-at">{{ fmtTime(r.at) }}</span>
            </div>
            <div v-if="r.nt" class="concl-note">{{ r.nt }}</div>
          </div>
        </div>
      </div>
    </el-card>
  </div>

  <PrecheckReport v-if="showPrecheckCards" :p="p" />
  <DupCheck v-if="showDupCards" :p="p" @refresh="emitRefresh" />
</div>
</template>

<style scoped>
@import 'base.css';

/* 评审会票数：四格并排，票数用大字，一眼看出过半没过半 */
.vote-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.vote-box {
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  padding: 12px 10px;
  text-align: center;
  background: var(--panel-2);
}

.vote-num {
  font-size: 24px;
  line-height: 1.2;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.vote-label {
  font-size: 12px;
  color: var(--ink-4);
  margin-top: 4px;
}

.vote-sum {
  background: var(--panel);
  border-color: var(--line);
}

.vote-sum .vote-num {
  color: var(--brand, var(--ink));
}

.expert-line {
  font-size: 11.5px;
  color: var(--ink-3);
  margin-top: 10px;
}

/* 评审会材料：一份一行，类型标签固定宽度，文件名长了省略，
   否则七八张表一列下来长短不齐，找某位专家的那张要一行行读 */
.review-files-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
  color: var(--ink-2);
}


/* 费用评审轮次 */
.cf-list {
  display: flex;
  flex-direction: column;
}

.cf-row {
  display: flex;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px dashed var(--line-soft);
}

.cf-row:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.cf-round {
  flex: 0 0 56px;
  font-size: 12px;
  color: var(--brand-deep);
  background: var(--brand-bg);
  border-radius: 6px;
  text-align: center;
  padding: 2px 0;
  height: 20px;
  line-height: 16px;
}

.cf-body {
  flex: 1;
  min-width: 0;
}

.cf-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.cf-act {
  font-size: 12.5px;
  color: var(--ink);
  font-weight: 500;
}

.cf-act.ca-cost_review_return {
  color: var(--warn);
}

.cf-act.ca-cost_review_pass {
  color: var(--ok);
}

.cf-act.ca-cost_review_reject {
  color: var(--danger);
}

.cf-actor {
  font-size: 12px;
  color: var(--ink-3);
}

.cf-time {
  font-size: 12px;
  color: var(--ink-4);
  margin-left: auto;
}

.cf-items {
  margin: 5px 0 0;
  padding-left: 18px;
  font-size: 11.5px;
  color: var(--ink-2);
  line-height: 1.8;
}

.cf-note {
  font-size: 11.5px;
  color: var(--ink-2);
  line-height: 1.8;
  margin-top: 4px;
  white-space: pre-wrap;
}


/* 环节视图开关 */
/* 环节结论 */
.concl-list {
  display: flex;
  flex-direction: column;
}

.concl-row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 14px;
  padding: 11px 12px;
  border-bottom: 1px dashed var(--line-soft);
}

.concl-row:last-child {
  border-bottom: 0;
}

.concl-label {
  font-size: 12px;
  color: var(--ink-3);
  padding-top: 1px;
}

.concl-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.concl-val {
  font-size: 13.5px;
  color: var(--ink);
  font-weight: 500;
}

.concl-at {
  font-size: 12px;
  color: var(--ink-4);
}

.concl-note {
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.75;
  margin-top: 3px;
}
</style>
