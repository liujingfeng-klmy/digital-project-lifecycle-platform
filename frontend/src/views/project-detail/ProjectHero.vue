<script setup>
import { computed } from 'vue'
import { ORDER, STAGE_HINT } from '@/utils/projectFormat'
import { stepStatesOf, progressPctOf } from '@/utils/projectDetailModel'
import { useUserStore } from '@/stores/user'

/* 项目信息带：编号、阶段、名称、关键要素一屏说清，下面是阶段进度。
 * 原来进度条、阶段名、停留天数各占一行，说的是同一件事 —— 收成一条带子。 */
const props = defineProps({
  p: { type: Object, required: true },
  viewOnly: Boolean,
  terminal: Boolean,
  canEdit: Boolean,
  canExport: Boolean,
  canDelete: Boolean,
  exporting: Boolean,
  missingRequired: { type: Array, default: () => [] },
})
const emit = defineEmits(['edit', 'export', 'delete', 'unlock'])

const userStore = useUserStore()
const stepStates = computed(() => stepStatesOf(props.p))
const progressPct = computed(() => progressPctOf(props.p))

/* 按钮都在这一带里，动作本身留在外壳：删除要确认、导出要下载，都是整页级的事 */
const unlock = () => emit('unlock')
const openEdit = () => emit('edit')
const exportArchive = () => emit('export')
const removeProject = () => emit('delete')
</script>

<template>
<section class="hero">
  <div class="hero-top">
    <div class="hero-head">
      <div class="hero-id">
        <span class="hero-code mono">{{ p.code }}</span>
        <span class="hero-stage">
          <i class="hs-dot" :class="'d-' + p.stage" />{{ p.stage_label }}
        </span>
        <span v-if="p.round" class="hero-round">第 {{ p.round }} 轮</span>
      </div>
      <h1 class="hero-title">{{ p.name }}</h1>
      <div class="hero-meta">
        <span>{{ p.unit }}</span>
        <i class="hm-sep" />
        <span>{{ p.year || '—' }} 年度</span>
        <i class="hm-sep" />
        <span>{{ p.category || '未填领域' }}</span>
        <i class="hm-sep" />
        <span>申报人 {{ p.applicant_display }}</span>
      </div>
    </div>
    <div class="hero-actions">
      <span v-if="viewOnly" class="ro-badge">只读</span>
      <el-button
        v-if="viewOnly && userStore.isAdmin"
        size="small" @click="unlock"
      >以管理员身份办理</el-button>
      <el-button v-if="canEdit" size="small" @click="openEdit">修改申报信息</el-button>
      <el-button v-if="canExport" size="small" :loading="exporting" @click="exportArchive">
        导出归档包
      </el-button>
      <el-button v-if="canDelete" size="small" class="hero-danger" @click="removeProject">删除</el-button>
    </div>
  </div>

  <div class="hp">
    <div class="hp-bar"><div class="hp-fill" :style="{ width: progressPct + '%' }" /></div>
    <div class="hp-steps">
      <span
        v-for="(s, i) in ORDER"
        :key="s.key"
        class="hp-step"
        :class="{
          done: stepStates[i] === 'done',
          now: stepStates[i] === 'now',
          stop: stepStates[i] === 'stop',
        }"
      >{{ s.label }}</span>
    </div>
  </div>

  <!-- 阶段说明只对「还要办」的环节有意义；结项、终止后阶段名已说明一切 -->
  <div v-if="!terminal" class="hero-hint">
    {{ STAGE_HINT[p.stage] || '' }}
    <span v-if="p.stage !== 'accepted' && p.stage !== 'rejected'" class="hero-stay">
      已在此阶段 {{ p.days_in_stage }} 天
    </span>
  </div>
</section>

<!-- 储备库不是终态：说明停在哪儿、怎么接着走，别让人以为项目被作废了 -->
<el-alert
  v-if="p.stage === 'reserve'"
  type="info"
  :closable="false"
  show-icon
  class="miss-bar"
  title="本项目暂无资金安排，已转入项目储备库"
>
  <template #default>
    待财政安排资金后，由审核岗在「本环节办理」点「重新纳入财政审核」，
    从财政审核接着往下走；左侧「项目储备库」里随时能找到它。
  </template>
</el-alert>

<el-alert
  v-if="missingRequired.length"
  type="warning"
  :closable="false"
  show-icon
  class="miss-bar"
  :title="`申报信息还缺：${missingRequired.join('、')}。补齐后才能提交预审。`"
>
  <template #default>
    <el-button link type="primary" size="small" @click="openEdit">去补填</el-button>
  </template>
</el-alert>
</template>

<style scoped>
@import 'base.css';

.miss-bar {
  margin-top: 12px;
}


/* 项目头图：浅蓝渐变卡 + 右侧网格纹理，信息靠字重和间距立住，不压深底 */
.hero {
  position: relative;
  border-radius: 18px;
  padding: 22px var(--pad-x) 18px;
  border: 1px solid #cfe0f5;
  background: linear-gradient(115deg, #f3f8ff 0%, #ffffff 52%, #f7fbff 100%);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}

.hero::after {
  content: "";
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(29, 95, 174, 0.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(29, 95, 174, 0.055) 1px, transparent 1px);
  background-size: 28px 28px;
  -webkit-mask-image: linear-gradient(100deg, transparent 28%, #000 100%);
  mask-image: linear-gradient(100deg, transparent 28%, #000 100%);
  pointer-events: none;
}

.hero-top {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  position: relative;
  z-index: 1;
}

.hero-id {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.hero-code {
  font-size: 11.5px;
  color: var(--ink-3);
  letter-spacing: 0.02em;
}

.hero-stage {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  font-weight: 500;
  height: 23px;
  padding: 0 8px;
  border-radius: 6px;
  background: var(--primary-soft);
  color: var(--primary-deep);
  border: 1px solid var(--primary-line);
}

.hs-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary);
  display: inline-block;
}

.d-supplement,
.d-rejected {
  background: #e5484d;
}

.d-cost_review,
.d-fiscal_review,
.d-acceptance {
  background: #e58c2d;
}

.d-expert_review,
.d-review_supplement {
  background: #7c5ce0;
}

.d-accepted {
  background: #12b76a;
}

.d-approval,
.d-precheck,
.d-building,
.d-trial {
  background: var(--primary-2);
}

.hero-round {
  font-size: 12px;
  color: #8a5a00;
  background: #fff8e6;
  padding: 1px 7px;
  border-radius: 5px;
  font-weight: 500;
}

.hero-title {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.45;
  margin: 10px 0 8px;
  letter-spacing: -0.3px;
  color: var(--ink);
}

.hero-meta {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  font-size: 12.5px;
  color: var(--ink-3);
}

.hm-sep {
  width: 1px;
  height: 11px;
  background: var(--line-strong);
}

.hero-actions {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
  padding-top: 2px;
  position: relative;
  z-index: 1;
}

.hero-actions :deep(.el-button) {
  background: #fff;
  border-color: var(--line-strong);
  color: var(--ink-2);
}

.hero-actions :deep(.el-button:hover) {
  background: var(--surface-2);
  border-color: #b9c9dc;
  color: var(--ink);
}

.hero-actions :deep(.hero-danger) {
  color: var(--danger);
  border-color: #f1c6c2;
}

.hero-actions :deep(.hero-danger:hover) {
  background: var(--danger-soft);
  border-color: #ecb4af;
  color: var(--danger);
}

.ro-badge {
  align-self: center;
  font-size: 12px;
  color: var(--ink-3);
  border: 1px solid var(--line-strong);
  border-radius: 4px;
  padding: 2px 7px;
  background: #fff;
}


.hp {
  margin-top: 20px;
  position: relative;
  z-index: 1;
}

.hp-bar {
  height: 4px;
  background: #e3eaf3;
  border-radius: 2px;
  overflow: hidden;
}

.hp-fill {
  height: 100%;
  background: linear-gradient(90deg, #2f80ed, #1d5fae);
  border-radius: 2px;
  transition: width 0.4s;
}

.hp-steps {
  display: flex;
  justify-content: space-between;
  margin-top: -4px;
}

.hp-step {
  position: relative;
  flex: 1;
  text-align: center;
  font-size: 12px;
  color: var(--ink-4);
  padding-top: 12px;
  line-height: 1.3;
}

/* 节点圆心压在轨道中轴上：轨道 4px 高，中轴在 top:2px，
   translateY(-50%) 让圆点（含 1.5px 边框）以该处为中心，不再整体偏上 */
.hp-step::before {
  content: "";
  position: absolute;
  top: 2px;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #fff;
  border: 1.5px solid #c3d3e5;
}

.hp-step.done {
  color: var(--ink-2);
}

.hp-step.done::before {
  background: var(--primary);
  border-color: var(--primary);
}

.hp-step.now {
  color: var(--primary-deep);
  font-weight: 600;
}

.hp-step.now::before {
  background: var(--primary);
  border-color: #fff;
  box-shadow: 0 0 0 3px rgba(29, 95, 174, 0.16);
}

.hp-step.stop {
  color: var(--danger);
}

.hp-step.stop::before {
  background: #e5484d;
  border-color: #e5484d;
}

.hero-hint {
  margin-top: 12px;
  padding-top: 11px;
  border-top: 1px solid var(--line);
  font-size: 12px;
  line-height: 1.75;
  color: var(--ink-2);
  position: relative;
  z-index: 1;
}

.hero-stay {
  color: var(--warning);
  margin-left: 6px;
}
</style>
