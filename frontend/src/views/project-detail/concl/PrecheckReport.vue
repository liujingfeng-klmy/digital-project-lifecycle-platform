<script setup>
import { computed, ref } from 'vue'
import { fmtTime, sevClass, sevLabel } from '@/utils/projectFormat'

/* 预审结果：一级规则查形式、二级 AI 查内容，都不下结论 —— 结论在「初审通过/退回」。
 * 两块内容共用一个页签组，是因为它们回答同一个问题：这份可研有没有硬伤。 */
const props = defineProps({ p: { type: Object, required: true } })

const tab = ref('rule')
const ruleReport = computed(() => (props.p?.reports || []).find((r) => r.level === 'rule'))
const aiReport = computed(() => (props.p?.reports || []).find((r) => r.level === 'ai'))

/* 规则项按审查维度分组：一条条平铺下来，看不出「哪一块毛病最多」 */
const ruleGroups = computed(() => {
  const items = ruleReport.value?.items || []
  const groups = []
  for (const it of items) {
    let g = groups.find((x) => x.name === it.group)
    if (!g) {
      g = { name: it.group, items: [] }
      groups.push(g)
    }
    g.items.push(it)
  }
  return groups
})
</script>

<template>
<!-- 预审结果：只在预审与评审期出现 -->
<el-card shadow="never" class="card">
  <template #header>
    <div class="card-head">
      <span class="card-title">预审结果</span>
      <span class="card-sub">规则查形式，AI 查内容，都不下结论</span>
    </div>
  </template>
  <el-tabs v-model="tab">
    <el-tab-pane label="一级 · 规则预审" name="rule">
      <template v-if="ruleReport">
        <div class="report-head">
          <span class="verdict" :class="'vd-' + ruleReport.verdict">{{ ruleReport.verdict_label }}</span>
          <span class="muted">
            第 {{ ruleReport.round + 1 }} 轮审查 · {{ ruleReport.created_by_name }} · {{ fmtTime(ruleReport.created) }}
          </span>
          <span class="counts">
            <b class="c-block">{{ ruleReport.counts.block }}</b> 必须补正
            <b class="c-warn">{{ ruleReport.counts.warn }}</b> 需说明
            <b class="c-pass">{{ ruleReport.counts.pass }}</b> 通过
          </span>
        </div>
        <div v-for="g in ruleGroups" :key="g.name" class="rgroup">
          <div class="rgroup-name">{{ g.name }}</div>
          <div v-for="it in g.items" :key="it.code + it.title" class="ritem">
            <span class="sev" :class="sevClass(it.severity)">{{ sevLabel(it.severity) }}</span>
            <div class="rbody">
              <div class="rtitle">{{ it.title }}</div>
              <div class="rdetail">{{ it.detail }}</div>
              <div v-if="it.advice" class="radvice">处理建议：{{ it.advice }}</div>
            </div>
          </div>
        </div>
        <div class="report-note">{{ ruleReport.note }}</div>
      </template>
      <el-empty v-else description="还没做过规则预审" :image-size="64">
        <div class="empty-tip">初审岗点右侧「执行规则预审」，几秒出结果。</div>
      </el-empty>
    </el-tab-pane>

    <el-tab-pane label="二级 · AI 深度审查" name="ai">
      <template v-if="aiReport">
        <div class="report-head">
          <span class="verdict" :class="'vd-' + aiReport.verdict">{{ aiReport.verdict_label }}</span>
          <span class="muted">
            第 {{ aiReport.round + 1 }} 轮审查 · {{ aiReport.llm_info?.model || '模型未记录' }} · {{ fmtTime(aiReport.created) }}
          </span>
          <span class="counts">
            <b class="c-block">{{ aiReport.counts.block || 0 }}</b> 必须补正
            <b class="c-warn">{{ aiReport.counts.warn || 0 }}</b> 需说明
            <b class="c-info">{{ aiReport.counts.info || 0 }}</b> 仅供参考
            <span class="c-total">共 {{ aiReport.counts.total }} 条</span>
          </span>
        </div>
        <div v-if="aiReport.counts.unread" class="unread-alert">
          有 {{ aiReport.counts.unread }} 段材料模型没审成（输出被截断或格式异常），
          这部分内容不能当作「没问题」，需人工补看。详见下方说明。
        </div>
        <div v-for="(f, i) in aiReport.findings" :key="i" class="finding">
          <div class="fhead">
            <span class="sev" :class="sevClass(f.level)">{{ sevLabel(f.level) }}</span>
            <span class="ftitle">{{ f.title }}</span>
            <span class="fsection">{{ f.section }}</span>
          </div>
          <div v-if="f.quote" class="fquote">「{{ f.quote }}」</div>
          <div class="fissue">{{ f.issue }}</div>
          <div v-if="f.advice" class="fadvice">建议：{{ f.advice }}</div>
        </div>
        <div class="report-note">{{ aiReport.note }}</div>
      </template>
      <el-empty v-else description="还没做过 AI 审查" :image-size="64">
        <div class="empty-tip">
          需先在「系统设置」里配置模型并启用。AI 审查按章节逐段过，几十秒出结果。
        </div>
      </el-empty>
    </el-tab-pane>
  </el-tabs>
</el-card>
</template>

<style scoped>
@import '../base.css';

.verdict {
  font-size: 11.5px;
  padding: 2px 9px;
  border-radius: 10px;
}

.vd-pass {
  background: var(--ok-bg);
  color: var(--ok);
}

.vd-attention {
  background: var(--warn-bg);
  color: var(--warn);
}

.vd-block {
  background: var(--danger-bg);
  color: var(--danger);
}

.counts {
  font-size: 11.5px;
  color: var(--ink-3);
  margin-left: auto;
}

.counts b {
  font-family: var(--font-mono);
  font-weight: 500;
  margin-left: 8px;
}

.c-block {
  color: var(--danger);
}

.c-warn {
  color: var(--warn);
}

.c-pass {
  color: var(--ok);
}

.c-info {
  color: var(--ink-3);
}

.c-total {
  margin-left: 10px;
  padding-left: 10px;
  border-left: 1px solid var(--line-soft);
}


.rgroup {
  margin-bottom: 12px;
}

.rgroup-name {
  font-size: 12px;
  color: var(--ink-4);
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}

.ritem {
  display: flex;
  gap: 9px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--line-soft);
}

.ritem:last-child {
  border-bottom: 0;
}

.sev {
  font-size: 12px;
  padding: 1px 7px;
  border-radius: 4px;
  height: fit-content;
  white-space: nowrap;
  flex-shrink: 0;
}

.sv-block {
  background: var(--danger-bg);
  color: var(--danger);
}

.sv-warn {
  background: var(--warn-bg);
  color: var(--warn);
}

.sv-pass {
  background: var(--ok-bg);
  color: var(--ok);
}

.rbody {
  min-width: 0;
}

.rtitle {
  font-size: 12.5px;
  color: var(--ink);
}

.rdetail {
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.75;
  margin-top: 2px;
}

.radvice {
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.7;
  margin-top: 2px;
}

.unread-alert {
  margin-top: 8px;
  padding: 6px 9px;
  border-radius: 4px;
  background: var(--warn-bg);
  color: var(--warn);
  font-size: 11.5px;
  line-height: 1.7;
}


.finding {
  padding: 9px 0;
  border-bottom: 1px dashed var(--line-soft);
}

.finding:last-of-type {
  border-bottom: 0;
}

.fhead {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ftitle {
  font-size: 12.5px;
  color: var(--ink);
  flex: 1;
}

.fsection {
  font-size: 12px;
  color: var(--ink-4);
}

.fquote {
  font-size: 12px;
  color: var(--ink-3);
  background: var(--panel-2);
  border-left: 2px solid var(--line);
  padding: 4px 8px;
  margin: 6px 0;
  line-height: 1.7;
}

.fissue {
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.8;
}

.fadvice {
  font-size: 11.5px;
  color: var(--brand-deep);
  line-height: 1.7;
  margin-top: 3px;
}
</style>
