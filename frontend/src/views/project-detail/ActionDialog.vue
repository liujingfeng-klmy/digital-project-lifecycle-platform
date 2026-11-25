<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { projectApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { useUiStore } from '@/stores/ui'
import { fmtTime } from '@/utils/projectFormat'
import { downloadDoc, viewReviewFile as viewReviewFileApi } from '@/utils/projectFiles'
import { conclusionModel, fmtSaved } from '@/utils/projectDetailModel'

/* 办理动作弹窗：一个动作一张表。
 *
 * 这里装着三件容易混在一起的事，所以单独成构件：
 *  1. 表单取值与校验 —— 不同动作字段不同，规则也不一样（票数须为单数、
 *     补正事项每条至少 4 个字、撤回原因至少 5 个字）；
 *  2. 草稿 —— 填一半存下来，件停在在办箱，下次点进来原样带回；
 *  3. 转出选人 —— 一个岗位上有几个人，选一个人件才进他的待办。
 */
const props = defineProps({ p: { type: Object, required: true } })
const emit = defineEmits(['refresh'])

const userStore = useUserStore()
const ui = useUiStore()

const actShow = ref(false)
const act = ref(null)
const actForm = reactive({
  note: '', deadline: '', items: [],
  votes: { agree: 0, modified: 0, reject: 0 },
  agency: '', doc_no: '', approval_no: '',
  trial_summary: '', data_collect_scope: '',
})
const submitting = ref(false)
/** 这次动作在弹窗里有没有表单：没有表单的（如「转建设中」）只要选人，
 *  确认文案直接写在弹窗里，不再叠一个二次确认框 */
const actHasForm = ref(false)
const draftLoaded = ref(false)
const savingDraft = ref(false)

/* ------------------------------------------------------------ 办理草稿
 * 填到一半有事走开：点「保存，稍后继续」存下来，件停在「在办箱」，
 * 下次点进来原样带回。草稿不是流转动作 —— 不改阶段、不进留痕、
 * 不影响别人的待办；提交成功后服务端会把这一环节的草稿清掉。
 */
const myDraft = computed(() => props.p?.my_draft || null)
const draftSavedAt = computed(() => fmtSaved(myDraft.value?.updated))
/** 这张表存过草稿吗：换了动作就不算，别把另一个动作的草稿当成这儿的 */
const draftForAct = computed(() => (myDraft.value && act.value
  && myDraft.value.action_key === act.value.key ? myDraft.value : null))

/** 把草稿内容摊回表单：票数、问题清单这类结构化字段要合并，
 *  直接整体赋值会把表单里的默认结构冲掉 */
function applyDraft(payload) {
  for (const [k, v] of Object.entries(payload || {})) {
    if (k === 'votes' && v && typeof v === 'object') {
      Object.assign(actForm.votes, {
        agree: +v.agree || 0, modified: +v.modified || 0, reject: +v.reject || 0,
      })
    } else if (k === 'items' && Array.isArray(v)) {
      actForm.items = v.length ? v : [{ title: '', requirement: '' }]
    } else if (k === 'assignee') {
      assignPick.value = v
    } else {
      actForm[k] = v
    }
  }
}

/** 原样取值，不做必填与字数校验 —— 存草稿就是存半成品，拦着不让存等于没这功能 */
function rawActPayload(a) {
  const payload = {}
  for (const f of a.form || []) {
    let v = actForm[f.key]
    if (f.type === 'items') v = (actForm.items || []).filter((x) => (x.title || '').trim())
    if (f.type === 'number' && (v === '' || v === null)) v = null
    if (f.type === 'votes') {
      v = {
        agree: +actForm.votes.agree || 0,
        modified: +actForm.votes.modified || 0,
        reject: +actForm.votes.reject || 0,
      }
    }
    if (v !== null && v !== undefined && v !== '') payload[f.key] = v
  }
  if (showAssign.value && assignPick.value) payload.assignee = assignPick.value
  return payload
}

async function saveDraft() {
  const a = act.value
  if (!a) return
  const payload = rawActPayload(a)
  const hasContent = Object.values(payload).some((v) => {
    if (Array.isArray(v)) return v.length > 0
    if (v && typeof v === 'object') return Object.values(v).some((x) => +x > 0)
    return String(v ?? '').trim() !== ''
  })
  if (!hasContent) {
    ElMessage.warning('还没填内容。写一点再保存；不想办可以点「取消」')
    return
  }
  savingDraft.value = true
  try {
    const { data } = await projectApi.saveDraft(props.p.id, {
      action_key: a.key, payload,
    })
    actShow.value = false
    ElMessage.success(data.detail || '已存入在办箱')
    emit('refresh')
    ui.refreshPending()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败，请稍后重试')
  } finally {
    savingDraft.value = false
  }
}

async function discardDraft() {
  try {
    await ElMessageBox.confirm(
      '丢弃这一环节存过的草稿？填过的内容不会保留。',
      '丢弃草稿',
      { type: 'warning', confirmButtonText: '丢弃', cancelButtonText: '取消' })
  } catch { return }
  try {
    await projectApi.dropDraft(props.p.id)
    actShow.value = false
    ElMessage.success('草稿已丢弃')
    emit('refresh')
    ui.refreshPending()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '丢弃失败')
  }
}

/* ------------------------------------------------------------ 转出选人
 * 一个岗位上有几个人，只按岗位转出等于把件丢进一个池子。动作里带着
 * 目标环节的候选人清单，转出时选一个人，这一件就只进他的待办。
 */
const assignPick = ref(null)

const assignEntry = computed(() => {
  const list = act.value?.assign || []
  if (!list.length) return null
  if (list.length === 1) return list[0]
  // 评审会结论决定去向：通过转审核岗、修改后通过转申报单位、不通过归档。
  // 这里跟着已经填的票数切换候选人，不让人选完人再被结论推翻
  const v = actForm.votes || {}
  const total = (+v.agree || 0) + (+v.modified || 0) + (+v.reject || 0)
  const yes = (+v.agree || 0) + (+v.modified || 0)
  if (!total) return null
  let target = 'rejected'
  if (yes * 2 > total) target = (+v.modified || 0) > 0 ? 'review_supplement' : 'cost_review'
  return list.find((x) => x.stage === target) || null
})

const showAssign = computed(() => !!assignEntry.value && !assignEntry.value.fixed
  && (assignEntry.value.candidates || []).length > 0)

// 换目标环节（或换结论）时，原来选的人如果不在新名单里就重新挑一个：
// 默认落在自己身上，其次第一个账号
watch(assignEntry, (e) => {
  if (!e || e.fixed) {
    assignPick.value = null
    return
  }
  const ids = (e.candidates || []).map((c) => c.id)
  if (!ids.includes(assignPick.value)) {
    const me = userStore.user?.id
    assignPick.value = ids.includes(me) ? me : (ids[0] ?? null)
  }
})

/** 填入模板语句：已经写过的先问一句，别把用户手打的内容冲掉 */
async function fillSample(f) {
  const cur = (actForm[f.key] || '').trim()
  if (cur) {
    try {
      await ElMessageBox.confirm('这一栏已经写了内容，用示例覆盖？', f.label,
        { confirmButtonText: '覆盖', cancelButtonText: '保留原内容' })
    } catch { return }
  }
  actForm[f.key] = f.sample
}

/* 评审会材料：附件走 multipart，不能塞进 JSON 流转请求里 */
const reviewFiles = computed(() => props.p?.review_files || [])
const reviewUploading = ref(false)
// 多选时 el-upload 会连着回调好几次，排队串行传：
// 每个响应都带完整项目，并发回来谁后到谁覆盖，中间传的那几份就看不见了
let uploadQueue = Promise.resolve()
function uploadReviewFiles(file, kind) {
  const fd = new FormData()
  fd.append('files', file.raw || file)
  fd.append('kind', kind)
  uploadQueue = uploadQueue.then(async () => {
    reviewUploading.value = true
    try {
      const { data } = await projectApi.reviewFilesUpload(props.p.id, fd)
      ElMessage.success(data.detail || '已上传')
      emit('refresh')
    } catch { /* 错误提示由拦截器统一弹出 */ } finally {
      reviewUploading.value = false
    }
  })
  return uploadQueue
}

async function removeReviewFile(f) {
  try {
    await ElMessageBox.confirm(`删除《${f.name}》？删掉后判定依据就少一份。`,
      '删除评审会材料', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  try {
    await projectApi.reviewFileDelete(props.p.id, f.id)
    ElMessage.success('已删除')
    emit('refresh')
  } catch { /* 错误提示由拦截器统一弹出 */ }
}

const viewReviewFile = (f) => viewReviewFileApi(props.p.id, f)

/** 录入结论时，「修改要求」只对「修改后通过」有意义，其他结论不必填 */
const showReviewItems = computed(() => {
  if (act.value?.key !== 'conclude') return true
  return (+actForm.votes.modified || 0) > 0
})

/** 票数校验：合计必须为正的单数，否则会平票。后端也拦，这里先提示免得白填 */
const voteTip = computed(() => {
  const v = actForm.votes || {}
  const total = (+v.agree || 0) + (+v.modified || 0) + (+v.reject || 0)
  if (!total) {
    return { level: 'idle', text: '按签字表填写三档人数，合计须为单数（3、5、7…）' }
  }
  if (total % 2 === 0) {
    return {
      level: 'bad',
      text: `合计 ${total} 人为偶数，会出现平票。评审会应为单数（3、5、7…）`,
    }
  }
  const yes = (+v.agree || 0) + (+v.modified || 0)
  const ok = yes * 2 > total
  return {
    level: 'ok',
    text: `合计 ${total} 人，通过（含修改后通过）${yes} 人 —— ${ok ? '过半，评审通过' : '未过半，评审不通过'}`,
  }
})

function addItem() {
  actForm.items.push({ title: '', requirement: '' })
}

function rmItem(i) {
  actForm.items.splice(i, 1)
}

const aiBlock = computed(() => {
  const ai = (props.p?.reports || []).find((r) => r.level === 'ai')
  return ai?.counts?.block || 0
})

/** 本轮（征求反馈后这一轮）已上传的材料数。允许什么都不补直接提交，
 *  只在「真的空手提交」时提醒一句后果，不拦人 */
const roundFileCount = computed(() => (props.p?.files || [])
  .filter((f) => f.round === props.p?.round).length)

async function submitAction() {
  const a = act.value
  /* 材料已改成「按份存」：专家意见表、汇总意见表各存一条 ReviewFile。
     这里必须查 review_files，只查改造前的单份 review_scan 会让新传的扫描件
     全部判成「没传」，弹出提示却怎么传都过不去。老项目的单份扫描件仍认 */
  if (a.key === 'conclude' && !reviewFiles.value.length && !props.p.review_scan) {
    ElMessage.warning('请先上传评审会材料（专家意见表或汇总意见表），再提交结论')
    return
  }
  /* 多选时文件是排队串行上传的，最后一份还在路上就点确认，
     服务端会判定为「没传」。等队列落地再放行 */
  if (a.key === 'conclude' && reviewUploading.value) {
    ElMessage.warning('评审会材料还在上传，传完再提交结论')
    return
  }
  if (a.key === 'conclude' && voteTip.value.level !== 'ok') {
    ElMessage.warning(voteTip.value.text)
    return
  }
  const payload = {}
  for (const f of a.form || []) {
    let v = actForm[f.key]
    if (f.type === 'items') {
      v = (actForm.items || []).filter((x) => (x.title || '').trim().length >= 4)
      // 条目太短的静默丢掉会让用户以为写进去了，明确提示
      const short = (actForm.items || []).filter((x) => (x.title || '').trim()
        && (x.title || '').trim().length < 4)
      if (short.length) {
        ElMessage.warning('每条补正/反馈事项至少 4 个字，太短说不清要改什么')
        return
      }
    }
    if (f.type === 'number' && (v === '' || v === null)) v = null
    if (f.type === 'votes') {
      v = {
        agree: +actForm.votes.agree || 0,
        modified: +actForm.votes.modified || 0,
        reject: +actForm.votes.reject || 0,
      }
    }
    // 必填空.value 按 trim 后判断：纯空格后端会拒，别让用户白跑一趟
    const empty = v === '' || v === null || v === undefined
      || (typeof v === 'string' && !v.trim())
      || (Array.isArray(v) && !v.length)
    if (f.required && empty) {
      ElMessage.warning(`请填写${f.label}`)
      return
    }
    // withdraw 的 form 元数据没写 min，但后端要求撤回原因至少 5 个字
    const minLen = f.min || (a.key === 'withdraw' && f.type !== 'items' ? 5 : 0)
    // min 只约束「真写了内容」的字段：留空与否由 required 说了算，
    // 否则可留空的说明类字段会被长度校验拦住，逼着凑字数
    if (minLen && typeof v === 'string' && v.trim() && v.trim().length < minLen) {
      ElMessage.warning(`${f.label}至少 ${minLen} 个字，太短的说明等于没写`)
      return
    }
    if (v !== null && v !== undefined && v !== '') payload[f.key] = v
  }
  if (showAssign.value && assignPick.value) payload.assignee = assignPick.value
  /* 费用补正允许什么都不补直接提交（单位认了评估结论、也确实没材料要补）。
     只在「真的一份材料、一句说明都没有」时提示一句后果 —— 提示，不是拦。
     弹过一次就够，别再叠后端的通用确认框，连点两下「确认」纯属折腾 */
  let confirmed = false
  if (a.key === 'cost_resubmit'
    && !(payload.note || '').trim() && roundFileCount.value === 0) {
    try {
      await ElMessageBox.confirm(
        '本轮既没有上传材料，也没有填写说明。系统会记为「对评估结论无异议，'
        + '无需补充材料」，由审核岗按现有材料重新评估，仍可能再次发起反馈。确认提交？',
        '未补充任何内容',
        { confirmButtonText: '确认提交', cancelButtonText: '返回补充' })
      confirmed = true
    } catch { return }
  }
  // 弹窗顶部已经原样列出了确认文案，不再叠第二个确认框；
  // 没有弹窗的动作（纯确认类）根本走不到这里
  if (a.confirm && !confirmed && !actHasForm.value && !showAssign.value) {
    try {
      await ElMessageBox.confirm(a.confirm, a.label,
        { confirmButtonText: '执行', cancelButtonText: '取消' })
    } catch { return }
  }
  await runAction(a, payload)
}

async function runAction(a, payload) {
  submitting.value = true
  try {
    const { data } = await projectApi.flow(props.p.id, { action: a.key, ...payload })
    actShow.value = false
    ElMessage.success(data.detail)
    emit('refresh')
    // 转出之后件不在我手上了：待办与在办两个角标都要跟着变
    ui.refreshPending()
  } finally {
    submitting.value = false
  }
}

/** 由外壳调用：把这张表摊开（包括「没表单也没选人」的纯确认类动作） */
async function open(a) {
  const hasForm = (a.form || []).length > 0
  const needAssign = (a.assign || []).some((x) => !x.fixed)
  // 既没表单、也不用选人的动作，一个确认框就够
  if (!hasForm && !needAssign) {
    const tip = a.confirm || `确认执行「${a.label}」？`
    try {
      await ElMessageBox.confirm(tip, a.label, { confirmButtonText: '执行', cancelButtonText: '取消' })
    } catch { return }
    await runAction(a, {})
    return
  }

  act.value = a
  actHasForm.value = hasForm
  assignPick.value = null
  Object.assign(actForm, {
    note: '', deadline: '', items: [],
    votes: { agree: 0, modified: 0, reject: 0 },
    agency: '', doc_no: '', approval_no: '',
    // 退回重填时必须清空上一轮的试运行/归集说明，否则旧文字会留在框里
    trial_summary: '', data_collect_scope: '',
  })
  if ((a.form || []).some((f) => f.type === 'items')) {
    actForm.items = [{ title: '', requirement: '' }]
  }
  /* 金额类的默认值：评估核定投资带可研投资（经办人按报告改），
     财政核定投资带财评结论（财政不改就沿用）。空着让人自己填，
     十有八九会抄错一位数，而这是批复上唯一的数字 */
  const hasKey = (k) => (a.form || []).some((f) => f.key === k)
  if (hasKey('audit_amount')) actForm.audit_amount = props.p?.budget_wan ?? null
  if (hasKey('budget_amount')) actForm.budget_amount = props.p?.budget_wan ?? null
  // 重录结论时把已录票数带出来，免得从头数一遍
  const v = conclusionModel(props.p).votes
  if ((a.form || []).some((f) => f.type === 'votes') && v.recorded) {
    actForm.votes = { agree: v.agree, modified: v.modified, reject: v.reject }
  }
  /* 存过草稿就把内容带回来：同一环节同一动作才回填，
     换了动作说明上次存的不是这张表，硬填会串内容 */
  draftLoaded.value = false
  if (myDraft.value && myDraft.value.action_key === a.key) {
    applyDraft(myDraft.value.payload)
    draftLoaded.value = true
  }
  actShow.value = true
}

defineExpose({ open })
</script>

<template>
<!-- 动作表单 -->
<el-dialog
  v-model="actShow"
  :title="act?.label"
  width="600px"
  :close-on-click-modal="false"
>
  <el-form label-position="top">
    <el-alert
      v-if="draftLoaded"
      type="success"
      :closable="false"
      show-icon
      class="act-alert"
      :title="`已带入 ${draftSavedAt} 保存的草稿，改完点「确认」提交，或再存一次`"
    />
    <el-alert
      v-if="act?.confirm"
      type="info"
      :closable="false"
      show-icon
      class="act-alert confirm-alert"
      :title="act.confirm"
    />
    <el-form-item v-if="assignEntry && !assignEntry.fixed && !showAssign" label="承办人">
      <el-alert
        type="warning" :closable="false" show-icon
        :title="`${assignEntry.scope_label}还没有可用账号，转出后只能进岗位待办`"
      >
        <template #default>
          这一件转出后没有任何人会收到待办。请先到「账号管理」按
          {{ assignEntry.scope_label }}建号，再回来转出。
        </template>
      </el-alert>
    </el-form-item>
    <el-form-item v-if="showAssign" label="转交给谁承办">
      <el-select v-model="assignPick" filterable style="width: 100%"
        placeholder="不选则由该岗位任何人接办">
        <el-option
          v-for="c in assignEntry.candidates"
          :key="c.id"
          :label="`${c.display}（${c.role_label}）`"
          :value="c.id"
        >
          <span>{{ c.display }}</span>
          <span class="opt-role">
            {{ c.role_label }}<template v-if="c.unit"> · {{ c.unit }}</template>
          </span>
        </el-option>
      </el-select>
      <div class="field-tip">
        转出后这一件只进所选人的待办；名单里没有合适的人，说明该岗位还没建账号，
        先到「账号管理」按角色建号再转。
      </div>
    </el-form-item>
    <el-form-item v-if="act?.key === 'conclude'" label="评审会材料（必传，可多份）">
      <div class="scan-box scan-box-col">
        <div v-if="reviewFiles.length" class="review-files">
          <div v-for="f in reviewFiles" :key="f.id" class="review-file">
            <span class="rf-kind" :class="f.kind">{{ f.kind_label }}</span>
            <el-button link type="primary" size="small" class="rf-name"
              @click="viewReviewFile(f)">{{ f.name }}</el-button>
            <el-button link type="danger" size="small"
              @click="removeReviewFile(f)">删</el-button>
          </div>
        </div>
        <div class="upload-row">
          <el-upload
            :auto-upload="false" :show-file-list="false" multiple
            accept=".pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff"
            :on-change="(f) => uploadReviewFiles(f, 'expert')"
          >
            <el-button size="small" :loading="reviewUploading">
              + 上传专家意见表
            </el-button>
          </el-upload>
          <el-upload
            :auto-upload="false" :show-file-list="false" multiple
            accept=".pdf,.jpg,.jpeg,.png,.webp,.tif,.tiff"
            :on-change="(f) => uploadReviewFiles(f, 'summary')"
          >
            <el-button size="small" :loading="reviewUploading">
              + 上传汇总意见表
            </el-button>
          </el-upload>
        </div>
        <span class="scan-tip">
          每位专家一张表就传一张，汇总表另传，可一次选多份。支持 PDF 或图片，单份 30MB 内。
          一张都没传不能出结论 —— 签字件是原始凭证。
        </span>
      </div>
    </el-form-item>
    <el-alert
      v-if="act?.key === 'precheck_pass' && aiBlock"
      type="warning"
      :closable="false"
      show-icon
      class="act-alert"
      :title="`AI 深度审查提出 ${aiBlock} 项「必须补正」意见`"
    >
      <template #default>
        系统不阻断通过（模型可能误判），但意见会随件流转并记入留痕，
        请专家在评审时逐项核定。
      </template>
    </el-alert>
    <template v-for="f in act?.form || []" :key="f.key">
      <el-form-item v-if="f.type === 'votes'" :label="f.label">
        <div class="vote-form">
          <div class="vote-input">
            <span class="vote-input-label">通过</span>
            <el-input-number v-model="actForm.votes.agree" :min="0" :max="99"
              controls-position="right" style="width: 96px" />
          </div>
          <div class="vote-input">
            <span class="vote-input-label">修改后通过</span>
            <el-input-number v-model="actForm.votes.modified" :min="0" :max="99"
              controls-position="right" style="width: 96px" />
          </div>
          <div class="vote-input">
            <span class="vote-input-label">不通过</span>
            <el-input-number v-model="actForm.votes.reject" :min="0" :max="99"
              controls-position="right" style="width: 96px" />
          </div>
        </div>
        <div class="field-tip" :class="{ 'tip-bad': voteTip.level === 'bad',
          'tip-ok': voteTip.level === 'ok' }">{{ voteTip.text }}</div>
      </el-form-item>

      <el-form-item v-else-if="f.type === 'items'" v-show="showReviewItems" :label="f.label">
        <div class="items">
          <div v-for="(it, i) in actForm.items" :key="i" class="item-row">
            <div class="item-no">{{ i + 1 }}</div>
            <div class="item-fields">
              <el-input v-model="it.title" placeholder="要改什么，例如：补充投资概算分项明细" />
              <el-input v-model="it.requirement" placeholder="具体要求（可选）" size="small" />
            </div>
            <el-button link type="danger" @click="rmItem(i)">删</el-button>
          </div>
          <el-button size="small" plain @click="addItem">+ 添加一条</el-button>
        </div>
        <div class="field-tip">每条至少 4 个字，申报单位照着这张单子改。</div>
      </el-form-item>

      <el-form-item v-else-if="f.type === 'date'" v-show="showReviewItems" :label="f.label">
        <el-date-picker v-model="actForm.deadline" type="date" value-format="YYYY-MM-DD"
          placeholder="选择日期" style="width: 100%" />
      </el-form-item>

      <el-form-item v-else-if="f.type === 'number'" :label="f.label" :required="f.required">
        <div class="num-field">
          <el-input-number v-model="actForm[f.key]" :min="0" :max="999999"
            :precision="2" :controls="false" style="width: 180px" />
          <span class="num-unit">万元</span>
        </div>
        <div v-if="f.tip" class="field-tip">{{ f.tip }}</div>
      </el-form-item>

      <el-form-item v-else-if="f.type === 'text'" :label="f.label" :required="f.required">
        <el-input v-model="actForm[f.key]" />
      </el-form-item>

      <el-form-item v-else :label="f.label" :required="f.required">
        <el-input
          v-model="actForm[f.key]"
          type="textarea"
          :rows="f.sample ? 5 : (f.key === 'note' ? 4 : 2)"
          :placeholder="f.placeholder || (f.min ? `至少 ${f.min} 个字` : '')"
        />
        <!-- 第一次报的项目单位不知道该写什么：给一段能照着改的模板语句 -->
        <div v-if="f.sample" class="sample-line">
          <el-button link type="primary" size="small" @click="fillSample(f)">
            填入示例
          </el-button>
          <span class="sample-tip">填入模板语句，改日期与数字即可</span>
        </div>
        <div v-if="f.tip" class="field-tip">{{ f.tip }}</div>
      </el-form-item>
    </template>
  </el-form>
  <el-alert
    v-if="act?.key === 'precheck_pass'"
    type="info"
    :closable="false"
    show-icon
    title="初审意见会写入流转记录，评审与费用评审环节都能看到。有硬性缺项时系统会拦住，只能退回补正。"
  />
  <!-- 保存放在最左、和确认同一条底栏：弹窗再长也不用来回滚动找按钮 -->
  <template #footer>
    <div class="act-footbar">
      <el-button v-if="draftForAct" link type="danger" @click="discardDraft">丢弃草稿</el-button>
      <el-button class="af-save" :loading="savingDraft" @click="saveDraft">
        保存，稍后继续
      </el-button>
      <span class="af-grow" />
      <el-button @click="actShow = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submitAction">确认</el-button>
    </div>
  </template>
</el-dialog>
</template>

<style scoped>
@import 'base.css';

.opt-role {
  font-size: 11.5px;
  color: var(--ink-4);
  margin-left: 8px;
}

.scan-box-col {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}

.upload-row {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}


/* 动作条：实线断开正文，标签在左、按钮在右 */
/* 弹窗底栏：保存常驻左侧，不用滚到最底下找 */
.act-footbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.act-footbar .af-save {
  margin-left: 4px;
}

.af-grow {
  flex: 1 1 auto;
}

.items {
  width: 100%;
}

.item-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-bottom: 8px;
}

.item-no {
  width: 18px;
  font-size: 12px;
  color: var(--ink-4);
  padding-top: 8px;
}

.item-fields {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sample-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
}

.sample-tip {
  font-size: 11.5px;
  color: var(--ink-4);
}

.field-tip {
  font-size: 12px;
  color: var(--ink-4);
  line-height: 1.7;
  margin-top: 4px;
}

.tip-bad {
  color: var(--danger);
}

.tip-ok {
  color: var(--ok);
}


/* 录票：三档并排，标签在数字左边，填的时候不用来回看表头 */
.vote-form {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  width: 100%;
}

.vote-input {
  display: flex;
  align-items: center;
  gap: 8px;
}

.vote-input-label {
  font-size: 12.5px;
  color: var(--ink-2);
  white-space: nowrap;
}


.scan-box {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.scan-name {
  font-size: 12.5px;
  color: var(--ink-2);
}

.scan-tip {
  font-size: 12px;
  color: var(--ink-4);
}


/* 2026-11-24：左侧资料目录撤掉。材料在「项目资料」页签与侧栏「项目资料」页
   都查得到，详情页再挂一份常驻目录，等于同一屏摆两套入口，还把正文挤窄。 */

/* 金额输入：数字框 + 单位，别让人猜是元还是万元 */
.num-field { display: flex; align-items: center; gap: 8px; }

.num-unit { font-size: 12.5px; color: var(--ink-3); }
</style>
