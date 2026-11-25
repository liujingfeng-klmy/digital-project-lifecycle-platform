<script setup>
import { computed } from 'vue'
import { fmtTime } from '@/utils/projectFormat'
import { flowGroupsOf } from '@/utils/projectDetailModel'

/* 流转记录：一个环节一条（退回补正单列），按时间正序 —— 读流程的人是从头往下看的。
 * 想看清每一笔动作和每条经办意见，走右上角「逐条流水」，那一页才逐条摊。 */
const props = defineProps({ p: { type: Object, required: true } })
const emit = defineEmits(['go-flow'])

const flowGroups = computed(() => flowGroupsOf(props.p))
const goFlow = () => emit('go-flow')
</script>

<template>
<div class="tab-pane">
  <div class="tl">
    <div v-for="g in flowGroups" :key="g.key" class="tl-row" :class="{ 'is-ret': g.ret }">
      <span class="tl-dot" />
      <div class="tl-body">
        <div class="tl-line">
          <span class="tl-stage">{{ g.stageLabel }}</span>
          <span v-if="g.retTag" class="tl-ret-tag">退回补充材料</span>
          <span class="tl-act">{{ g.title }}</span>
          <span class="tl-actor">{{ g.actors || '系统' }}</span>
        </div>
        <div v-if="g.note" class="tl-note">{{ g.note }}</div>
        <div v-if="g.n > 1" class="tl-sub">本环节 {{ g.n }} 次操作：{{ g.acts.join(' · ') }}</div>
      </div>
      <!-- 时间独立成列、贴卡片右端：原来跟在标题群里 flex-wrap，
           文字一长就换行，时间和标题挤在同一行，读起来像两段字叠在一起 -->
      <span class="tl-time">{{ fmtTime(g.at) }}</span>
    </div>
  </div>
</div>
</template>

<style scoped>
@import 'base.css';

/* 流转记录：最新一条在最上面，时间线只留一条竖线 */
.tl {
  display: flex;
  flex-direction: column;
}

.tl-row {
  position: relative;
  display: flex;
  gap: 12px;
  padding-bottom: 16px;
}

.tl-row:last-child {
  padding-bottom: 0;
}

.tl-row::before {
  content: "";
  position: absolute;
  left: 3px;
  top: 15px;
  bottom: 0;
  width: 1px;
  background: var(--line);
}

.tl-row:last-child::before {
  display: none;
}

.tl-dot {
  flex: 0 0 7px;
  width: 7px;
  height: 7px;
  margin-top: 7px;
  border-radius: 50%;
  background: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-bg);
}

.tl-body {
  flex: 1;
  min-width: 0;
  /* 一行意见写满整张卡（一千多像素）读起来要横着甩头，读到行尾找不到下一行。
     收到 820 以内，右边留出边界，时间也跟着落在同一条竖线上 */
  max-width: min(820px, 78%);
}

.tl-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.tl-act {
  font-size: 13.5px;
  color: var(--ink);
  font-weight: 500;
}

.tl-actor {
  font-size: 12px;
  color: var(--ink-3);
}

.tl-time {
  /* 时间是独立一列、贴卡片右端：跟在标题群里 flex-wrap 时，标题一长就换行，
     时间落回标题中间，看着像和文字交叉。单独成列后无论正文多长都停在最右，
     正文的阅读宽度仍由 .tl-body 限住，两边互不影响 */
  flex: 0 0 auto;
  margin-left: auto;
  padding-top: 1px;
  font-size: 11.5px;
  line-height: 18px;
  color: var(--ink-4);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.tl-note {
  font-size: 12.5px;
  color: var(--ink-2);
  line-height: 1.75;
  margin-top: 4px;
  white-space: pre-line;
}

/* 环节名做成固定宽度的小标签：一列下来环节对得齐，
   一眼能扫出「走到哪、这步办成了什么」 */
.tl-stage {
  flex: 0 0 auto;
  min-width: 62px;
  font-size: 11.5px;
  line-height: 18px;
  padding: 0 6px;
  border-radius: 3px;
  background: var(--panel-3);
  color: var(--ink-3);
  text-align: center;
  white-space: nowrap;
}

/* 退回补正单独一条，颜色拉开：这不是进度，是一次重来的起点 */
.tl-row.is-ret .tl-dot {
  background: var(--warn);
  box-shadow: 0 0 0 3px var(--warn-bg);
}

.tl-ret-tag {
  font-size: 11.5px;
  line-height: 18px;
  padding: 0 6px;
  border-radius: 3px;
  background: var(--warn-bg);
  color: var(--warn);
  white-space: nowrap;
}

.tl-sub {
  margin-top: 3px;
  font-size: 11.5px;
  color: var(--ink-4);
  line-height: 1.7;
}
</style>
