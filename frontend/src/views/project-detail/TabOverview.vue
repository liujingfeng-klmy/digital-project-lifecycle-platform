<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { fmtMoney, fmtTime, ORDER } from '@/utils/projectFormat'
import { stageIndexOf } from '@/utils/projectDetailModel'
import { useTextBlock } from '@/composables/useTextBlock'

/* 项目概况：概要表 + 三段长正文。只读就得像公文一样好读，
 * 不摆任何办理入口 —— 要办去「本环节办理」。 */
const props = defineProps({
  p: { type: Object, required: true },
  viewOnly: Boolean,
  terminal: Boolean,
})

const { isBlockOpen, toggleBlock, isLongText, blockText } = useTextBlock()

const eventsAsc = computed(() => [...(props.p?.events || [])])

/* 概况表列数跟着内容区宽度走，不跟窗口走：窄屏还硬撑两列，每列三百出头，
   标签和值挤成一团。量的是整页容器 .wrap（左资料栏删掉之后，
   原来量的 .detail-main 已经不存在了）。 */
const descCols = ref(2)
let descRO = null

function watchMainWidth() {
  const el = document.querySelector('.wrap')
  if (!el) return
  const apply = (w) => { descCols.value = w >= 800 ? 2 : 1 }
  apply(el.getBoundingClientRect().width)
  if (!window.ResizeObserver) return
  descRO?.disconnect()
  descRO = new ResizeObserver(([e]) => apply(e.contentRect.width))
  descRO.observe(el)
}

onMounted(async () => {
  await nextTick()
  watchMainWidth()
})
onBeforeUnmount(() => descRO?.disconnect())
watch(() => props.p?.id, async () => {
  await nextTick()
  watchMainWidth()
})

/* 核定预算与可研投资的差额：一个「核减 8.6 万元」比两个数字并排让人自己减清楚得多 */
const budgetDiff = computed(() => {
  const b = props.p?.budget_amount
  const q = props.p?.investment
  if (b === null || b === undefined || q === null || q === undefined) return ''
  const d = Number(b) - Number(q)
  if (!d) return ''
  return `${d < 0 ? '核减' : '核增'} ${fmtMoney(Math.abs(d))} 万元`
})

/* 核定预算为空时的说明：费用评审之前是「还没核」，费用评审之后（含历史件）
 * 是「当时没记这个数」，批复按可研总投资出 —— 两种情况写同一句「待核定」，
 * 会让人以为卡在这一步没办。 */
const budgetPendingText = computed(() => {
  const cr = ORDER.findIndex((s) => s.key === 'cost_review')
  const cur = stageIndexOf(props.p)
  const passed = cur < 0 ? true : cur > cr
  return passed ? '未单独核定，批复按可研总投资' : '待费用评审核定'
})
</script>

<template>
<div class="tab-pane pane-info">
  <!-- ③ 项目情况：立项环节收起，切「完整档案」再看。
       这里不再单独加「项目情况」分区标签 —— 下面第一张卡的标题就是
       「项目概况」，两行字说的是同一件事，白占一行。 -->
  <div class="zone info-zone">
  <!-- 回看时「办理信息」卡在内侧的办理页签里，这里补一行合上；
       结项后「当前办理：已结束」没有意义，不显示 -->
  <div v-if="viewOnly && !terminal" class="ro-line">
    <span>当前办理：<b>{{ p.handler_label || '—' }}</b></span>
    <span v-if="eventsAsc.length" class="ro-last">
      最近：{{ eventsAsc[eventsAsc.length - 1].action_label }}
      <span class="ro-time">{{ fmtTime(eventsAsc[eventsAsc.length - 1].created) }}</span>
    </span>
  </div>
  <!-- 概况：页签已经叫「项目概况」，卡头不再重复这四个字 -->
  <el-card shadow="never" class="card">
    <el-descriptions :column="descCols" size="small" class="info-desc">
      <el-descriptions-item label="申报单位">{{ p.unit }}</el-descriptions-item>
      <el-descriptions-item label="申报年度">{{ p.year || '—' }}</el-descriptions-item>
      <el-descriptions-item label="联系人">{{ p.contact_name || '—' }}</el-descriptions-item>
      <el-descriptions-item label="联系人手机号">{{ p.contact_phone || '—' }}</el-descriptions-item>
      <el-descriptions-item label="业务领域">{{ p.category || '—' }}</el-descriptions-item>
      <el-descriptions-item label="可研总投资">
        {{ fmtMoney(p.investment) }} 万元
      </el-descriptions-item>
      <!-- 核定预算：费用评审按评估报告核定，财政审核可再调，批复就按这个数写。
           与可研申报数并排摆，差多少一眼看得出 -->
      <el-descriptions-item label="核定预算">
        <template v-if="p.budget_amount != null">
          <b class="budget-num">{{ fmtMoney(p.budget_amount) }}</b> 万元
          <span v-if="budgetDiff" class="budget-diff">{{ budgetDiff }}</span>
        </template>
        <span v-else class="muted">{{ budgetPendingText }}</span>
      </el-descriptions-item>
      <el-descriptions-item label="建设周期">{{ p.duration_months || '—' }} 个月</el-descriptions-item>
      <el-descriptions-item label="提交时间">{{ fmtTime(p.submitted_at) }}</el-descriptions-item>
    </el-descriptions>
    <div class="block">
      <div class="block-label">
        <span>建设内容</span>
        <span class="block-len">{{ (p.summary || '').length }} 字</span>
      </div>
      <div class="clamp-block">{{ blockText(p.summary, 'summary') || '（未填写）' }}</div>
      <button v-if="isLongText(p.summary)" class="clamp-toggle" @click="toggleBlock('summary')">
        {{ isBlockOpen('summary') ? '收起' : '展开全文' }}
        <span>{{ isBlockOpen('summary') ? '▴' : '▾' }}</span>
      </button>
    </div>
    <div class="block">
      <div class="block-label">
        <span>建设必要性</span>
        <span class="block-len">{{ (p.necessity || '').length }} 字</span>
      </div>
      <div class="clamp-block">{{ blockText(p.necessity, 'necessity') || '（未填写）' }}</div>
      <button v-if="isLongText(p.necessity)" class="clamp-toggle" @click="toggleBlock('necessity')">
        {{ isBlockOpen('necessity') ? '收起' : '展开全文' }}
        <span>{{ isBlockOpen('necessity') ? '▴' : '▾' }}</span>
      </button>
    </div>
    <div class="block">
      <div class="block-label">
        <span>政策依据</span>
        <span class="block-len">{{ (p.basis || '').length }} 字</span>
      </div>
      <div class="clamp-block">{{ blockText(p.basis, 'basis') || '（未填写）' }}</div>
      <button v-if="isLongText(p.basis)" class="clamp-toggle" @click="toggleBlock('basis')">
        {{ isBlockOpen('basis') ? '收起' : '展开全文' }}
        <span>{{ isBlockOpen('basis') ? '▴' : '▾' }}</span>
      </button>
    </div>
  </el-card>

 </div>

</div>
</template>

<style scoped>
@import 'base.css';

.block {
  margin-top: 16px;
}

.block-label {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  color: var(--ink-3);
  letter-spacing: 0.03em;
  /* margin: auto; */
  /* padding: 10, 10, 10, 10; */
  padding: 10px;
  margin-bottom: 6px;
}

.block-len {
  font-size: 12px;
  color: var(--ink-4);
  font-family: var(--font-mono);
}

/* 长文默认收 7 行，点开铺满整页。原来给了一整块浅灰底加边框，
   看着像输入框；概况页签里就是读文字，纯排版更好读 */
.block .clamp-block {
  margin-top: 6px;
  /* margin: 10, 10, 10, 10; */
  padding: 10px;
}

.block .clamp-toggle {
  padding-left: 2px;
}

/* 概况表：两列、无边框，行间一条虚线，像公文里的事项表。
   左侧内边距清零 —— 表格标签、正文段落、页签文字都从 30px 起笔，
   原来标签比正文右缩 12px，一眼就看出没对齐。
   Element 的 size 规则选择器比这里长，不加 !important 压不住 */
.info-desc
  :deep(.el-descriptions__body .el-descriptions__table .el-descriptions__cell) {
  padding: 11px 14px 11px 10px !important;
  border-bottom: 1px dashed var(--line-soft);
}

/* 表格不跟着右栏一起变宽：卡片一千二，两列各六百，标签和值之间空出半行，
   读一行要横向跳两次。收到 920 以内，两列各 460，一眼能扫完 */
.info-desc { max-width: 920px; }

.info-desc :deep(.el-descriptions__label) {
  /* label 原来是 inline，width 设了不起作用：标签宽度跟着字数走
     （「申报单位」66px、「联系人」54px），后面的值就各起各的头。
     浮动 + 定宽，值统一从 104px 处起排，两列各有自己的竖线 */
  float: left;
  box-sizing: border-box;
  width: 104px;
  padding-right: 10px;
  color: var(--ink-3);
  font-weight: 400;
  white-space: nowrap;
}

/* 值用块级 + 左外边距：只把 label 定宽时，窄屏上值会被挤到下一行；
   定宽 inline-block 也不行（td 里 100% 算不出来）。块级最稳，长了自己换行 */
.info-desc :deep(.el-descriptions__content) {
  display: block;
  margin-left: 104px;
  color: var(--ink);
  font-size: 13.5px;
}

.info-desc :deep(.el-descriptions__cell)::after {
  content: '';
  display: block;
  clear: both;
}

.info-zone :deep(.el-card) {
  background: transparent;
}

/* 回看时的一行办理状态：不占一张卡，跟着概况走 */
.ro-line {
  display: flex;
  align-items: baseline;
  gap: 18px;
  flex-wrap: wrap;
  padding: 0 0 14px;
  margin-bottom: 4px;
  border-bottom: 1px dashed var(--line-soft);
  font-size: 12.5px;
  color: var(--ink-3);
}

.ro-line b {
  color: var(--ink);
  font-weight: 500;
}

.ro-last {
  margin-left: auto;
}

.ro-time {
  color: var(--ink-4);
  margin-left: 4px;
}


/* 核定预算与可研投资并排：核减多少直接写在后面，不用自己减 */
.budget-num { font-variant-numeric: tabular-nums; color: var(--ink); }

.budget-diff {
  margin-left: 6px; font-size: 11.5px; color: var(--warn, #B26A00);
  font-variant-numeric: tabular-nums;
}
</style>
