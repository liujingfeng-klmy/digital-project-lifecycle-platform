<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { showError } from '@/api/http'
import { projectApi } from '@/api'
import { downloadDoc } from '@/utils/projectFiles'

/* 预审修正说明：把规则预审与 AI 审查查出的问题勾选进函件。
 * 勾选状态按 key 存 —— 报告重跑后 key 会变，旧 key 自动清掉，不会张冠李戴。 */
const props = defineProps({ p: { type: Object, required: true } })
const emit = defineEmits(['refresh'])

const candidates = computed(() => props.p?.precheck_candidates || [])
const pickedMap = reactive({})
watch(candidates, (list) => {
  for (const c of list) {
    if (!(c.key in pickedMap)) pickedMap[c.key] = true
  }
  const alive = new Set(list.map((c) => c.key))
  Object.keys(pickedMap).forEach((k) => { if (!alive.has(k)) delete pickedMap[k] })
}, { immediate: true })

const pickedCount = computed(() => candidates.value.filter((c) => pickedMap[c.key]).length)
const allChecked = computed(() => candidates.value.length > 0
  && pickedCount.value === candidates.value.length)
const someChecked = computed(() => pickedCount.value > 0 && !allChecked.value)

function toggleAll(v) {
  for (const c of candidates.value) pickedMap[c.key] = !!v
}

const noticeDeadline = ref('')
const docBusy = ref(false)

async function genPrecheckNotice() {
  const keys = candidates.value.filter((c) => pickedMap[c.key]).map((c) => c.key)
  if (!keys.length) {
    ElMessage.warning('请至少勾选一条要写入说明的问题')
    return
  }
  docBusy.value = true
  try {
    const { data } = await projectApi.genPrecheckNotice(props.p.id, {
      keys, deadline: noticeDeadline.value || '',
    })
    ElMessage.success(data.detail)
    await downloadDoc(props.p.id, data.doc)
    emit('refresh')
  } catch (e) {
    showError(e?.response?.data?.detail || '生成失败')
  } finally {
    docBusy.value = false
  }
}
</script>

<template>
  <div v-if="!candidates.length" class="muted doc-empty">
    还没有可写入的问题。请先执行「规则预审」与「AI 深度审查」，
    系统会把必须补正、需说明的问题列到这里。
  </div>
  <template v-else>
    <div class="pick-head">
      <el-checkbox
        :model-value="allChecked"
        :indeterminate="someChecked"
        @change="toggleAll"
      >全选</el-checkbox>
      <span class="muted">已选 {{ pickedCount }} / {{ candidates.length }} 条</span>
    </div>
    <div class="pick-list">
      <div v-for="c in candidates" :key="c.key" class="pick-row">
        <el-checkbox v-model="pickedMap[c.key]" />
        <span class="pick-badge" :class="c.level">
          {{ c.level === 'block' ? '必须补正' : '需说明' }}
        </span>
        <span class="pick-src">{{ c.source_label }}</span>
        <span class="pick-title">{{ c.title }}</span>
        <span v-if="c.advice" class="pick-advice">{{ c.advice }}</span>
      </div>
    </div>
    <div class="dp-foot">
      <span class="foot-label">回函期限</span>
      <el-date-picker
        v-model="noticeDeadline"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="可留空，事后手填"
        style="width: 170px"
      />
      <el-button type="primary" :loading="docBusy" @click="genPrecheckNotice">
        生成预审修正说明
      </el-button>
    </div>
  </template>
</template>

<style scoped>
@import '../base.css';

/* ---------------------------------------------------------- 生成文书 */
.doc-empty {
  padding: 8px 0;
  font-size: 12.5px;
  line-height: 1.7;
}

.pick-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line-soft);
}

.pick-list {
  max-height: 320px;
  overflow-y: auto;
  margin: 4px 0 10px;
}

.pick-row {
  display: grid;
  grid-template-columns: 20px 66px 84px minmax(150px, 1fr);
  gap: 8px;
  align-items: baseline;
  padding: 7px 4px;
  border-bottom: 1px dashed var(--line-soft);
  cursor: pointer;
}

.pick-row:hover {
  background: var(--panel-3);
}

.pick-row :deep(.el-checkbox) {
  margin-right: 0;
}

.pick-badge {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
  white-space: nowrap;
  text-align: center;
}

.pick-badge.block {
  background: var(--danger-bg);
  color: var(--danger);
}

.pick-badge.warn {
  background: var(--warn-bg);
  color: var(--warn);
}

.pick-src {
  font-size: 12px;
  color: var(--ink-4);
  white-space: nowrap;
}

.pick-title {
  font-size: 12.8px;
  color: var(--ink);
  line-height: 1.5;
}

.pick-advice {
  grid-column: 4;
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.5;
}
</style>
