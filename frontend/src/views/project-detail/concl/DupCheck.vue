<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { showError } from '@/api/http'
import { projectApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { levelClass } from '@/utils/projectFormat'
import DupCompare from '@/components/DupCompare.vue'

/* 重复建设筛查：只列疑点，是否重复由专家核定。
 * 旧结构（version < 2）只存了疑点摘要，没有逐项对照 —— 那种结果指认不出
 * 「哪几条功能、哪几段原文重复」，所以留一个「重新比对」的入口。 */
const props = defineProps({ p: { type: Object, required: true } })
const emit = defineEmits(['refresh'])

const userStore = useUserStore()
const canRefreshDup = computed(() => ['reviewer', 'auditor', 'admin'].includes(userStore.role))

const dupOpen = ref({ 0: true })
const dupDrawer = ref(false)
const dupPick = ref(null)
const dupRefreshing = ref(false)

const dupHasDetail = computed(() => (props.p?.dup_check?.version || 0) >= 2)
const hasDetail = (r) => !!((r?.sections?.length) || (r?.functions?.pairs?.length)
  || (r?.snippets?.length))

const pct = (v) => Math.round((v || 0) * 100)
const toggleDup = (i) => { dupOpen.value[i] = !dupOpen.value[i] }
function openDupDrawer(r) {
  dupPick.value = r
  dupDrawer.value = true
}

async function refreshDup() {
  dupRefreshing.value = true
  try {
    const { data } = await projectApi.dupRefresh(props.p.id)
    dupOpen.value = { 0: true }
    ElMessage.success(
      data.dup_check?.ranking?.length ? '已重新比对，可展开逐项对照' : '已重新比对，没有可比对的历史材料',
    )
    emit('refresh')
  } catch (e) {
    showError(e?.response?.data?.detail || '重新比对失败')
  } finally {
    dupRefreshing.value = false
  }
}
</script>

<template>
<!-- 查重：只在预审与评审期出现 -->
<el-card shadow="never" class="card">
  <template #header>
    <div class="card-head">
      <span class="card-title">重复建设筛查</span>
      <span class="card-sub">只列疑点，是否重复由专家核定</span>
    </div>
  </template>
  <template v-if="p.dup_check && p.dup_check.level">
    <div class="dup-head">
      <span class="duplevel" :class="levelClass(p.dup_check.level)">
        {{ p.dup_check.level_label }}
      </span>
      <span class="dup-summary">{{ p.dup_check.summary }}</span>
      <span class="muted dup-time">
        已比 {{ p.dup_check.compared || 0 }} 个项目 · {{ p.dup_check.checked_at || '' }}
      </span>
      <el-button
        v-if="canRefreshDup"
        size="small"
        :loading="dupRefreshing"
        @click="refreshDup"
      >
        重新比对
      </el-button>
    </div>

    <el-alert
      v-if="p.dup_check.ranking?.length && !dupHasDetail"
      type="warning"
      :closable="false"
      show-icon
      class="dup-alert"
      title="这次结果只存了疑点摘要，没有逐项对照（旧版本生成）。点「重新比对」生成对照表。"
    />

    <div v-if="(p.dup_check.ranking || []).length" class="dup-list">
      <div v-for="(r, i) in p.dup_check.ranking" :key="i" class="dup-row">
        <div class="dup-row-head" @click="toggleDup(i)">
          <span class="dup-idx">{{ i + 1 }}</span>
          <div class="dup-namebox">
            <div class="dup-name">{{ r.project_name }}</div>
            <div class="dup-sub">
              {{ r.unit || '单位未填' }} · {{ r.year || '年度未填' }} ·
              {{ r.investment ? r.investment + ' 万元' : '投资未填' }}
              <template v-if="r.doc_type"> · {{ r.doc_type }}</template>
            </div>
          </div>
          <div class="dup-metrics">
            <span>功能重合 <b>{{ pct(r.function_overlap) }}%</b></span>
            <span class="sep">·</span>
            <span>疑点 <b>{{ (r.findings || []).length }}</b></span>
          </div>
          <span class="duplevel" :class="levelClass(r.level)">{{ r.level_label }}</span>
          <el-button
            v-if="hasDetail(r)"
            link
            type="primary"
            size="small"
            @click.stop="openDupDrawer(r)"
          >
            放大
          </el-button>
          <span class="dup-caret">{{ dupOpen[i] ? '▴' : '▾' }}</span>
        </div>

        <div v-show="dupOpen[i]" class="dup-body">
          <DupCompare
            v-if="hasDetail(r)"
            :item="r"
            :cur-name="p.name"
            :prev-name="r.project_name"
            :scope="p.dup_check.scope || {}"
          />
          <div v-else class="dup-legacy">
            <div v-for="(f, j) in r.findings" :key="j" class="dup-finding">
              <span class="dup-ftitle">{{ f.title }}</span>
              <span class="dup-fdetail">{{ f.detail }}</span>
              <div v-if="f.ask" class="dup-fask">需核实：{{ f.ask }}</div>
            </div>
            <div v-if="canRefreshDup" class="dup-legacy-foot">
              没有逐项对照，无法指认哪几条功能、哪几段原文重复。
              <el-button link type="primary" size="small" @click="refreshDup">
                重新比对生成
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div v-else-if="p.dup_check.hidden_note" class="muted">
      {{ p.dup_check.hidden_note }}
    </div>
    <div v-else class="muted">没有可比对的历史项目。</div>
    <div class="report-note">{{ p.dup_check.note }}</div>
  </template>
  <div v-else class="empty-tip">提交申报时自动筛查；也可以执行规则预审刷新。</div>
</el-card>

<!-- 重复点对照：整屏阅读 -->
<el-drawer
  v-model="dupDrawer"
  :title="dupPick ? `重复点对照 · ${dupPick.project_name}` : '重复点对照'"
  size="82%"
>
  <template v-if="dupPick">
    <div class="drawer-cur">
      本次申报：<b>{{ p.name }}</b>
      <span class="muted">（{{ p.unit }} · {{ p.year }} 年度）</span>
    </div>
    <DupCompare :item="dupPick" :cur-name="p.name" :prev-name="dupPick.project_name"
      :scope="p.dup_check.scope || {}" />
  </template>
</el-drawer>
</template>

<style scoped>
@import '../base.css';

.dup-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.duplevel {
  font-size: 11.5px;
  padding: 2px 9px;
  border-radius: 10px;
  white-space: nowrap;
}

.lv-attention {
  background: var(--danger-bg);
  color: var(--danger);
}

.lv-verify {
  background: var(--warn-bg);
  color: var(--warn);
}

.lv-ok {
  background: var(--ok-bg);
  color: var(--ok);
}

.dup-summary {
  font-size: 12.5px;
  color: var(--ink-2);
}

.dup-time {
  margin-left: auto;
}

.dup-alert {
  margin-bottom: 10px;
}


.dup-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dup-row {
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--panel-2);
  overflow: hidden;
}

.dup-row-head {
  display: flex;
  gap: 9px;
  align-items: center;
  flex-wrap: wrap;
  padding: 9px 12px;
  cursor: pointer;
}

.dup-row-head:hover {
  background: #f8fbff;
}

.dup-idx {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
  border-radius: 4px;
  background: var(--surface-3);
  color: var(--ink-3);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dup-namebox {
  flex: 1;
  min-width: 180px;
}

.dup-name {
  font-size: 12.5px;
  color: var(--ink);
}

.dup-sub {
  font-size: 12px;
  color: var(--ink-4);
  margin-top: 1px;
}

.dup-metrics {
  font-size: 11.5px;
  color: var(--ink-3);
  white-space: nowrap;
}

.dup-metrics b {
  color: var(--brand-deep);
  font-weight: 600;
}

.dup-metrics .sep {
  color: var(--ink-4);
  margin: 0 4px;
}

.dup-caret {
  font-size: 10px;
  color: var(--ink-4);
}

.dup-body {
  padding: 4px 12px 12px;
  border-top: 1px solid var(--line-soft);
  background: #fff;
}

.dup-legacy {
  padding-top: 8px;
}

.dup-legacy-foot {
  margin-top: 8px;
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.8;
}

.dup-finding {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.75;
}

.dup-ftitle {
  color: var(--danger);
  margin-right: 6px;
}

.dup-fdetail {
  color: var(--ink-2);
}

.dup-fask {
  font-size: 11.5px;
  color: var(--ink-3);
  margin-top: 2px;
}

.drawer-cur {
  font-size: 12.5px;
  color: var(--ink-2);
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px dashed var(--line-soft);
}
</style>
