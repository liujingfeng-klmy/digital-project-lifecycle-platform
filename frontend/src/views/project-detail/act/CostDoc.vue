<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { showError } from '@/api/http'
import { projectApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { useUiStore } from '@/stores/ui'
import { fmtTime } from '@/utils/projectFormat'
import { downloadDoc } from '@/utils/projectFiles'
import {
  costTaskDocOf, costTasksOf, costReportsOf, costReportThisRoundOf,
  fmtSaved,
} from '@/utils/projectDetailModel'

/* 费用评审：出任务书 → 送达财评机构 → 等报告（可保存挂在在办箱）→ 收报告。
 * 报告是这一环节的结论依据，「费用评审通过」没有它按不动。 */
const props = defineProps({
  p: { type: Object, required: true },
  viewOnly: Boolean,
})
const emit = defineEmits(['refresh', 'open-raw'])

const userStore = useUserStore()
const ui = useUiStore()

const costTasks = computed(() => costTasksOf(props.p))
const costTaskDoc = computed(() => costTaskDocOf(props.p))
const costReports = computed(() => costReportsOf(props.p))

/** 生成过就锁表单与按钮：同一套参数没必要重复生成，改财评机构或联系人才解锁 */
const costEditUnlocked = ref(false)
const costLocked = computed(() => !!costTaskDoc.value && !costEditUnlocked.value)

const costForm = reactive({
  entrust_unit: '',
  entrust_contact: '',
  entrust_phone: '',
  unit_contact: '',
  unit_phone: '',
  days: 7,
})

/** 任务书两组联系人先带默认值：委托单位＝我局经办人，建设单位＝申报人。
 *  留空也能生成（后端同样兜底），但空着让财评机构找不到对接人。 */
function fillCostContacts() {
  if (!props.p) return
  const me = userStore.user || {}
  if (!costForm.entrust_contact) costForm.entrust_contact = me.display || me.name || ''
  if (!costForm.entrust_phone) costForm.entrust_phone = me.phone || ''
  // 建设单位联系人优先用申报时填的那一位：账号名是登录名，不一定是经办人
  if (!costForm.unit_contact) {
    costForm.unit_contact = props.p.contact_name || props.p.applicant_display || ''
  }
  if (!costForm.unit_phone) {
    costForm.unit_phone = props.p.contact_phone || props.p.applicant_phone || ''
  }
}

/** 任务书参数预填：优先取上一次生成时存下的参数（meta），再兜底联系人。
 *  单位反馈完再进本环节时，原来四个框全空，等于让人凭记忆重填一遍。 */
function prefillCostForm() {
  if (!props.p) return
  const m = costTaskDoc.value?.meta || {}
  if (m.entrust_unit) costForm.entrust_unit = m.entrust_unit
  if (m.entrust_contact) costForm.entrust_contact = m.entrust_contact
  if (m.entrust_phone) costForm.entrust_phone = m.entrust_phone
  if (m.unit_contact) costForm.unit_contact = m.unit_contact
  if (m.unit_phone) costForm.unit_phone = m.unit_phone
  if (m.days) costForm.days = Number(m.days) || costForm.days
  fillCostContacts()
}

/** 保存时记下的任务书参数：没有生成过任务书时补回表单，不用凭记忆重填 */
function applyHoldPayload() {
  const d = props.p?.my_draft
  if (!d || d.action_key !== 'hold') return
  const m = d.payload || {}
  for (const k of ['entrust_unit', 'entrust_contact', 'entrust_phone',
    'unit_contact', 'unit_phone']) {
    if (!costForm[k] && m[k]) costForm[k] = m[k]
  }
  if (m.days) costForm.days = Number(m.days) || costForm.days
}

/* 项目每次重新加载都要按最新的任务书与草稿回填（原来挂在外壳的 load 里，
   面板独立之后改成盯 project 本身） */
watch(() => props.p, () => {
  prefillCostForm()
  applyHoldPayload()
}, { immediate: true })

/** 联系电话：手机 11 位，或带区号的固定电话（区号 010/02x 三位、其余四位，
 *  号码 7—8 位，可分机）。与后端 PHONE_RE 保持一致；先挡一道，省得提交回来才报错。 */
const PHONE_RE = /^(?:1[3-9]\d{9}|(?:010|02\d|0\d{3})-?\d{7,8}(?:-\d{1,6})?)$/
const phoneErr = reactive({ entrust: '', unit: '' })

/** 返回错误文案，空串表示没问题。错误直接挂在字段下方，
 *  比只在页面顶部弹一条提示更容易看见 —— 按钮在卡片底部，离顶部很远。 */
function phoneError(value) {
  const s = (value || '').trim().replace(/[\s　]/g, '').replace(/[－—]/g, '-')
  if (!s || PHONE_RE.test(s)) return ''
  return '手机填 11 位；固定电话带区号，如 0990-6220000'
}

const docBusy = ref(false)

async function genCostTask() {
  if (!costForm.entrust_unit.trim()) {
    ElMessage.warning('请填写受托的财评机构名称')
    return
  }
  phoneErr.entrust = phoneError(costForm.entrust_phone)
  phoneErr.unit = phoneError(costForm.unit_phone)
  if (phoneErr.entrust || phoneErr.unit) {
    ElMessage.warning('联系电话格式不对，请按提示修改后再生成')
    return
  }
  docBusy.value = true
  try {
    const { data } = await projectApi.genCostTask(props.p.id, { ...costForm })
    costEditUnlocked.value = false   // 生成完重新锁住，参数改了才再解锁
    ElMessage.success(data.detail)
    await downloadDoc(props.p.id, data.doc)
    emit('refresh')
  } catch (e) {
    showError(e?.response?.data?.detail || '生成失败')
  } finally {
    docBusy.value = false
  }
}

/** 只有审核岗与管理员能传报告：初审岗进到本环节是看客，给个上传按钮只会点出 403 */
const canUploadReport = computed(() => !props.viewOnly
  && ['auditor', 'admin'].includes(userStore.role))
const reportBusy = ref(false)

/** 传评估报告：类型固定，不看用户的材料类型下拉 —— 这一步只可能传报告 */
async function uploadCostReport(file) {
  const raw = file?.raw || file
  if (!raw) return
  reportBusy.value = true
  try {
    const fd = new FormData()
    fd.append('files', raw)
    fd.append('doc_type', 'cost_report')
    const { data } = await projectApi.upload(props.p.id, fd)
    const bad = (data.results || []).find((r) => !r.ok)
    if (bad) ElMessage.warning(`《${bad.filename}》未收下：${bad.message}`)
    else ElMessage.success('评估报告已上传')
    emit('refresh')
  } catch (e) {
    showError(e?.response?.data?.detail || '上传失败，请稍后重试')
  } finally {
    reportBusy.value = false
  }
}

async function removeReport(row) {
  try {
    await ElMessageBox.confirm(`确认删除《${row.filename}》？`, '删除评估报告',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  try {
    await projectApi.removeFile(props.p.id, row.id)
    ElMessage.success('已删除')
    emit('refresh')
  } catch (e) {
    showError(e?.response?.data?.detail || '删除失败')
  }
}

/* ------------------------------------------------------------ 保存（等报告）
 * 费用评审的 7 个自然日在财评机构手里。报告没回来之前，件本来就在我手上：
 * 存一条「保存」，它从待办挪进在办箱，不再天天催我，也不必被逼着提前转走。
 */
const myDraft = computed(() => props.p?.my_draft || null)
const draftSavedAt = computed(() => fmtSaved(myDraft.value?.updated))
const holdDraft = computed(() => myDraft.value?.action_key === 'hold')
const canHoldCost = computed(() => !props.viewOnly
  && props.p?.stage === 'cost_review'
  && !costReportThisRoundOf(props.p).length
  && ['auditor', 'admin'].includes(userStore.role))
const holding = ref(false)
const costDeadline = computed(() => costTaskDoc.value?.meta?.deadline || '')
const costDueText = computed(() => {
  if (!costDeadline.value) return ''
  const d = new Date(`${costDeadline.value}T00:00:00`)
  if (Number.isNaN(d.getTime())) return ''
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const days = Math.round((d - today) / 86400000)
  if (days > 0) return `还有 ${days} 天`
  if (days === 0) return '今天到期'
  return `已超 ${-days} 天`
})

async function holdCost() {
  if (!props.p) return
  holding.value = true
  try {
    const payload = {
      note: `已生成《费用评审任务书》并送达财评机构，等第三方评估报告`
        + `（评审期限 ${costForm.days} 个自然日）`,
      deadline: costDeadline.value,
      entrust_unit: costForm.entrust_unit,
      entrust_contact: costForm.entrust_contact,
      entrust_phone: costForm.entrust_phone,
      unit_contact: costForm.unit_contact,
      unit_phone: costForm.unit_phone,
      days: costForm.days,
    }
    const { data } = await projectApi.saveDraft(props.p.id, { action_key: 'hold', payload })
    ElMessage.success('已保存，件在在办箱')
    emit('refresh')
    ui.refreshPending()
  } catch (e) {
    showError(e?.response?.data?.detail || '保存失败，请稍后重试')
  } finally {
    holding.value = false
  }
}

const openRaw = (row) => emit('open-raw', row)
</script>

<template>
  <!-- 生成清单放表单上方：生成过就该一眼看到，不用翻到页面底部找 -->
  <div v-if="costTasks.length" class="task-list">
    <div class="task-list-head">
      <span>已生成 {{ costTasks.length }} 份任务书</span>
      <span class="muted">下载盖章后送达财评机构</span>
    </div>
    <div v-for="(d, i) in costTasks" :key="d.id" class="task-row">
      <el-button link type="primary" class="doc-name" @click="downloadDoc(d)">
        {{ d.name }}
      </el-button>
      <span v-if="i === 0" class="task-badge">最新</span>
      <span class="muted">{{ d.created_by_name }} · {{ fmtTime(d.created) }}</span>
      <span v-if="d.meta?.days" class="task-meta">
        {{ d.meta.days }} 天 · 交付期限 {{ d.meta.deadline }}
      </span>
    </div>
  </div>

  <div v-if="costLocked" class="doc-lock-tip">
    任务书已生成，参数锁定。要改财评机构、联系人或评审期限，
    先点下方「修改」，改完再重新生成。
  </div>

  <el-form label-width="118px" label-position="left" class="doc-form"
    :disabled="costLocked">
    <el-form-item label="财评机构" required>
      <el-input v-model="costForm.entrust_unit"
        placeholder="受托评估的机构名称，如：市财政投资评审中心" />
    </el-form-item>
    <el-form-item label="委托单位联系人">
      <div class="two-col">
        <el-input v-model="costForm.entrust_contact" placeholder="我局经办人" />
        <div class="phone-cell">
          <el-input v-model="costForm.entrust_phone" maxlength="20"
            :class="{ 'is-bad': phoneErr.entrust }"
            placeholder="手机或固话，如 0990-6220000"
            @input="phoneErr.entrust = ''" />
          <div v-if="phoneErr.entrust" class="ferr">{{ phoneErr.entrust }}</div>
        </div>
      </div>
    </el-form-item>
    <el-form-item label="建设单位联系人">
      <div class="two-col">
        <el-input v-model="costForm.unit_contact" placeholder="申报单位经办人" />
        <div class="phone-cell">
          <el-input v-model="costForm.unit_phone" maxlength="20"
            :class="{ 'is-bad': phoneErr.unit }"
            placeholder="手机或固话，如 0990-6220000"
            @input="phoneErr.unit = ''" />
          <div v-if="phoneErr.unit" class="ferr">{{ phoneErr.unit }}</div>
        </div>
      </div>
    </el-form-item>
    <el-form-item label="评审期限">
      <el-input-number v-model="costForm.days" :min="1" :max="90" />
      <span class="unit">个自然日（默认 7）</span>
    </el-form-item>
  </el-form>
  <div class="dp-foot">
    <span class="muted">
      {{ costLocked ? '已生成，参数已锁定。'
        : '生成后自动下载，落款为出具单位与当日日期。' }}
    </span>
    <el-button v-if="costLocked" @click="costEditUnlocked = true">修改</el-button>
    <el-button v-else-if="costTaskDoc" @click="costEditUnlocked = false">
      取消修改
    </el-button>
    <el-button :type="costTaskDoc ? 'default' : 'primary'" :loading="docBusy"
      :disabled="costLocked" @click="genCostTask">
      {{ costTaskDoc ? '重新生成任务书' : '生成费用评审任务书' }}
    </el-button>
  </div>

  <!-- 第三方报告：费用评审的结论写在报告里，评审通过前必须有这份材料 -->
  <div v-if="canUploadReport" class="report-box">
    <div class="report-head">
      <span class="report-title">第三方评估报告</span>
      <span class="report-sub">
        财评机构出具后上传，通过前必须有；扫描件也可以
      </span>
    </div>
    <div v-if="costReports.length" class="report-list">
      <div v-for="r in costReports" :key="r.id" class="report-row">
        <el-button link type="primary" class="doc-name" @click="openRaw(r)">
          {{ r.filename }}
        </el-button>
        <span class="report-meta">
          {{ r.uploaded_by_name || '' }} · {{ fmtTime(r.created) }}
        </span>
        <el-button link type="danger" class="report-del"
          @click="removeReport(r)">删除</el-button>
      </div>
    </div>
    <div v-else class="report-empty">
      还没有上传评估报告 —— 点「费用评审通过」会被拦下。
    </div>
    <div class="report-foot">
      <el-upload
        :auto-upload="false"
        :show-file-list="false"
        :on-change="uploadCostReport"
        accept=".pdf,.docx,.txt,.md,.xlsx,.xlsm"
      >
        <el-button size="small" :loading="reportBusy">上传评估报告</el-button>
      </el-upload>
      <span class="report-tip">PDF / DOCX，单份 200MB 内，可传多份</span>
    </div>
  </div>

  <!-- 保存：报告没回来，件本来就在我手上。点一下就存进在办箱，
       不必为「别挂着」提前转走 -->
  <div v-if="canHoldCost" class="hold-bar" :class="{ on: holdDraft }">
    <div class="hold-main">
      <span class="hold-title">
        {{ holdDraft ? '已保存，件在在办箱' : '件在办（等评估报告）' }}
      </span>
      <span v-if="holdDraft" class="hold-tag">保存于 {{ draftSavedAt }}</span>
      <span v-if="costDeadline" class="hold-tag"
        :class="{ over: costDueText.startsWith('已超') }">
        交付期限 {{ costDeadline }}
        <template v-if="costDueText"> · {{ costDueText }}</template>
      </span>
      <span v-if="!costTaskDoc" class="hold-tag hold-warn">先生成任务书</span>
    </div>
    <div class="hold-act">
      <el-button size="small" type="primary" :disabled="!costTaskDoc"
        :loading="holding" @click="holdCost">保存</el-button>
    </div>
  </div>
</template>

<style scoped>
@import '../base.css';

.doc-form {
  margin-bottom: 4px;
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  width: 100%;
  align-items: start;
}

/* 电话格单独包一层：出错时只标红电话框，不把同一行的联系人姓名一起标红 */
.phone-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.phone-cell :deep(.is-bad .el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--danger) inset;
}

.unit {
  font-size: 12px;
  color: var(--ink-3);
  margin-left: 8px;
}


/* 已生成的任务书清单：摆在表单上方，生成过一眼可见 */
.task-list {
  margin-bottom: 12px;
  padding: 8px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--panel-2);
}

.task-list-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  font-size: 12px;
  color: var(--ink-2);
  margin-bottom: 2px;
}

.task-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 0;
  border-top: 1px dashed var(--line-soft);
  flex-wrap: wrap;
}

.task-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--ok-bg);
  color: var(--ok);
  white-space: nowrap;
}

.task-meta {
  font-size: 11.5px;
  color: var(--ink-4);
  margin-left: auto;
  white-space: nowrap;
}

.doc-lock-tip {
  font-size: 11.5px;
  color: var(--ink-2);
  line-height: 1.7;
  background: var(--panel-3);
  border-radius: 4px;
  padding: 7px 10px;
  margin-bottom: 10px;
}


/* 第三方评估报告：费用评审的成果物，比任务书更需要一眼看到 */
.report-box {
  margin-top: 14px;
  padding: 10px 12px 9px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--panel-2);
}

.report-title {
  font-size: 13px;
  font-weight: 500;
}

.report-sub {
  font-size: 11.5px;
  color: var(--ink-4);
}

.report-list {
  margin-top: 6px;
}

.report-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 0;
  border-top: 1px dashed var(--line-soft);
  flex-wrap: wrap;
}

.report-del {
  margin-left: auto;
}

.report-empty {
  margin-top: 6px;
  font-size: 12px;
  color: var(--warn, #b25f00);
  background: var(--warn-bg, #fdf6e7);
  border-radius: 4px;
  padding: 6px 9px;
}

.report-foot {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 16px;
  flex-wrap: wrap;
}

.report-tip {
  font-size: 11.5px;
  color: var(--ink-4);
}


/* 保存条：一行状态 + 一颗「保存」，不铺说明文字 */
.hold-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px dashed var(--line-strong);
  border-radius: 8px;
  background: var(--panel);
}

.hold-bar.on {
  border-style: solid;
  border-color: var(--primary-line);
  background: var(--primary-soft, var(--brand-bg));
}

.hold-main {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.hold-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
}

.hold-tag {
  font-size: 11.5px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  background: var(--panel-3);
  color: var(--ink-3);
  white-space: nowrap;
}

.hold-bar.on .hold-tag {
  background: #fff;
  color: var(--primary-deep);
}

.hold-tag.over {
  background: var(--danger-bg);
  color: var(--danger);
}

.hold-tag.hold-warn {
  background: #FDF1E2;
  color: #A85B12;
}

.hold-bar.on .hold-tag.hold-warn {
  background: #FDF1E2;
  color: #A85B12;
}

.hold-act {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
</style>
