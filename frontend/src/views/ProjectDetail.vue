<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { orgApi, projectApi, userApi } from '@/api'
import { showError } from '@/api/http'
import { useUserStore } from '@/stores/user'
import { useUiStore } from '@/stores/ui'
import { useRawFile } from '@/composables/useRawFile'
import RawPreview from '@/components/RawPreview.vue'
import MaterialUploadDialog from '@/components/MaterialUploadDialog.vue'
import ProjectFields from '@/components/ProjectFields.vue'
import { downloadBlob } from '@/utils/fileview'
import { stageLabel } from '@/utils/projectFormat'
import {
  conclusionModel, flowGroupsOf, materialRowCountOf,
  collectDocOf, costReportsOf, costTaskDocOf,
} from '@/utils/projectDetailModel'
import ProjectHero from './project-detail/ProjectHero.vue'
import TabAct from './project-detail/TabAct.vue'
import TabOverview from './project-detail/TabOverview.vue'
import TabMaterials from './project-detail/TabMaterials.vue'
import TabConclusion from './project-detail/TabConclusion.vue'
import TabFlow from './project-detail/TabFlow.vue'
import ActionDialog from './project-detail/ActionDialog.vue'

/* 项目详情页的外壳：只管「这一件是什么、能做什么」——
 * 加载、页签、权限、几个整页级弹窗（修改申报信息、改派、上传材料）。
 *
 * 具体内容都在 project-detail/ 下按域拆开了：
 *   ProjectHero    顶部信息带与阶段进度
 *   TabAct         本环节办理（内含预审/费用评审/验收三块文书面板）
 *   TabOverview    项目概况
 *   TabMaterials   项目资料
 *   TabConclusion  审核结论（内含预审结果、重复建设筛查）
 *   TabFlow        流转记录
 *   ActionDialog   办理动作弹窗
 * 拆之前这里是 5500 行一个文件，改「验收材料」要在整页里找半天。
 */
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const ui = useUiStore()

const loading = ref(true)
const loadError = ref('')
const p = ref(null)
const tab = ref('rule')
const fullArchive = ref(false)

/* ============================================================ 回看 / 办理
 * 从「已办项目」点进来的是档案：这个件当时经我手，现在可能已经到别人桌上
 * （比如立项由初审岗出批复，审核岗点进来不该看到办理表单，按了也办不了）。
 * 所以这条路径只回看：动作、生文、上传、修改全收起来，留资料与留痕。
 * 待办点进来是办理，不动。
 * 管理员偶尔要补办或救场，给一个显式解锁 —— 跟任务书「修改」按钮同一个思路，
 * 默认锁着，点一下才解开，避免误把回看当办理。
 */
const isReview = computed(() => route.query.view === 'readonly')
const adminUnlock = ref(false)
/* 从台账点进来默认只读回看，但有一条例外：这件此刻就在我手上
 * （后端给了可用动作）。储备库就是典型 —— 审核岗从「项目储备库」点进去，
 * 页面却是只读的，「重新纳入财政审核」根本出不来，件就再也走不动了。
 */
const viewOnly = computed(() => isReview.value && !adminUnlock.value
  && !(p.value?.actions || []).length)

/* 终态不再有「本环节要办什么」：结项后「本环节已办 N 天」这类话全是噪音。
 * 已验收只留项目概况与资料入口；终止与储备要留结论，说明为什么停在这儿。 */
const TERMINAL_STAGES = ['accepted', 'rejected', 'reserve']
const terminal = computed(() => TERMINAL_STAGES.includes(p.value?.stage))
const archived = computed(() => p.value?.stage === 'accepted')

const actions = computed(() => (viewOnly.value ? [] : (p.value?.actions || [])))

/* 后端的硬门槛在按钮上先挡一道：与其点了收 400，不如直接置灰并写明缺什么。
   这里只列「缺某份材料/某次预审」这类可从详情数据判断的条件，
   票数等动态校验仍由办理弹窗管。 */
function actionBlockReason(a) {
  const docs = p.value?.documents || []
  switch (a.key) {
    case 'precheck_pass': {
      const rule = (p.value?.reports || []).find((r) => r.level === 'rule')
      if (!rule) return '还没有执行规则预审'
      if (rule.verdict === 'block') return '规则预审有必须补正项，请先退回补正'
      return ''
    }
    case 'cost_review_pass':
      if (!costTaskDocOf(p.value)) return '还没有生成《费用评审任务书》'
      if (!costReportsOf(p.value).length) return '还没有上传第三方《费用评估报告》'
      return ''
    case 'approval_issue':
      if (!docs.some((d) => d.kind === 'approval_reply')) return '还没有生成《立项批复》'
      return ''
    case 'accept':
      if (!collectDocOf(p.value)) return '还没有出具《数据归集证明》'
      return ''
    default:
      return ''
  }
}

/* ------------------------------------------------------------ 权限
 * 能不能改、能不能传、能不能删，三处口径不同，别合成一个「能不能办」。
 */
const canEdit = computed(() => {
  if (!p.value || viewOnly.value) return false
  if (userStore.role === 'admin') return p.value.stage === 'draft'
  return userStore.role === 'applicant'
    && p.value.applicant === userStore.user?.id
    && ['draft', 'supplement'].includes(p.value.stage)
})

/** 能否增删材料：评审后补正就是让换材料的，但项目信息与投资不许动 —— 改了评审结论就作废 */
const canUpload = computed(() => {
  if (!p.value || viewOnly.value) return false
  if (userStore.role === 'admin') return p.value.stage === 'draft'
  return userStore.role === 'applicant'
    && p.value.applicant === userStore.user?.id
    && ['draft', 'supplement', 'review_supplement', 'cost_supplement'].includes(p.value.stage)
})

/** 补正阶段的按钮写「补正材料」，草稿阶段写「申报材料」——同一个入口，两种说法 */
const uploadLabel = computed(() => (['supplement', 'review_supplement', 'cost_supplement']
  .includes(p.value?.stage) ? '上传补正材料' : '上传申报材料'))

/** 能否删除：管理员不限阶段；申报单位只能删自己的草稿。与列表页同一口径。 */
const canDelete = computed(() => {
  if (!p.value || viewOnly.value) return false
  if (userStore.role === 'admin') return true
  return userStore.role === 'applicant'
    && p.value.applicant === userStore.user?.id
    && p.value.stage === 'draft'
})

/** 能否导出归档包：只有结项（已验收）的件才打包给单位存档、报审计。
 *  办到一半的件导出的是半套材料，既不能当档案也不能当凭证，一律不给入口。 */
const canExport = computed(() => p.value?.stage === 'accepted')
const exporting = ref(false)

async function exportArchive() {
  if (!p.value) return
  exporting.value = true
  try {
    const { data } = await projectApi.archive(p.value.id)
    const d = new Date()
    const day = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
    downloadBlob(data, `${p.value.code}_项目归档_${day}.zip`)
    ElMessage.success('归档包已导出，含材料历史版本、生成文书与流转留痕')
  } catch (e) {
    showError(e?.response?.data?.detail || '导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}

/** 草稿阶段还缺哪些必填项。提交时才拦人，不如提前摆在页面上 */
const missingRequired = computed(() => {
  if (!p.value || !['draft', 'supplement'].includes(p.value.stage)) return []
  const out = []
  if ((p.value.name || '').trim().length < 4) out.push('项目名称')
  if (!p.value.unit) out.push('申报单位')
  if (!p.value.investment) out.push('总投资')
  if ((p.value.summary || '').trim().length < 30) out.push('建设内容（至少 30 字）')
  return out
})

/* ------------------------------------------------------------ 页签
 * 一个项目有五类信息：本环节要办什么、项目本身、申报材料、上游结论、流转经过。
 * 全铺在一页要拉两米长，谁都找不到重点 —— 收进页签，一屏只看一类。
 */
const ptab = ref('act')

const conclModel = computed(() => conclusionModel(p.value, { fullArchive: fullArchive.value }))
const materialCount = computed(() => materialRowCountOf(p.value))
const flowCount = computed(() => flowGroupsOf(p.value).length)

const tabs = computed(() => {
  const list = []
  // 只读回看没有「本环节」：这条页签摆出来只会让人以为按得动
  if (!viewOnly.value) list.push({ key: 'act', label: '本环节办理' })
  list.push({ key: 'info', label: '项目概况' })
  // 这个页签里不只有单位报的材料：还有批复、评估报告、归集证明等
  // 系统文书，叫「申报材料」会让人以为漏交了东西
  if (materialCount.value) list.push({ key: 'files', label: '项目资料', count: materialCount.value })
  if (conclModel.value.conclCount) {
    list.push({ key: 'concl', label: '审核结论', count: conclModel.value.conclCount })
  }
  // 数字跟着实际行数走：按环节归并后是 12 条，就不能还标 23
  if (flowCount.value) list.push({ key: 'flow', label: '流转记录', count: flowCount.value })
  return list
})

// 项目读回来才知道该停在哪：能办就停在办理，只读或已结项就停在概况。
// URL 带 ?tab=info 时以链接为准 —— 从消息、台账里直接指到某一页签。
// 换件和换 tab 都要跟：只盯 id 的话，同一件带上 ?tab=files 打开还停在办理页
const TAB_KEYS = ['act', 'info', 'files', 'concl', 'flow']
watch([() => p.value?.id, () => route.query.tab], ([id, want], old) => {
  if (!id) return
  const switched = !old || old[0] !== id
  if (want && TAB_KEYS.includes(want)) {
    // 指到的页签这一件没有（例如待验收不再有审核结论）就交给下面的兜底
    ptab.value = tabs.value.some((t) => t.key === want) ? want : ''
    return
  }
  if (switched) {
    ptab.value = (viewOnly.value || terminal.value) ? 'info' : 'act'
  }
})

// 当前页签被拿掉（阶段推进后结论清空、只读态切换）时落回第一个，别停在空白页
watch(tabs, (list) => {
  if (list.length && !list.some((t) => t.key === ptab.value)) ptab.value = list[0].key
})

function goFlow() {
  if (!p.value) return
  // from / view 一起带着走：从待办或已办（回看）进来的件，翻完流转记录
  // 回去时上下文不变，不会被扔进台账或突然露出办理动作
  const q = {}
  if (route.query.from) q.from = route.query.from
  if (route.query.view) q.view = route.query.view
  router.push({ name: 'project-flow', params: { id: p.value.id }, query: q })
}

/* ------------------------------------------------------------ 办理动作
 * 表单、草稿、转出选人全在 ActionDialog 里；外壳只负责把动作交给它。
 * 从「在办箱」点进来会带 ?action=xxx，直接把那张表摊开。
 */
const actionDlg = ref(null)
let autoOpenedKey = ''

function openAction(a) {
  actionDlg.value?.open(a)
}

/** 从「在办箱」跳过来时，把存过的那张表直接摊开 */
function resumeDraft() {
  const d = p.value?.my_draft
  if (!d) return
  if (d.action_key === 'hold') {
    ElMessage.info('这件已在在办箱，等评估报告回来再办')
    return
  }
  const a = (p.value?.actions || []).find((x) => x.key === d.action_key)
  if (a) openAction(a)
}

function unlockAdmin() {
  adminUnlock.value = true
  ptab.value = 'act'
}

/* ------------------------------------------------------------ 原件预览
 * 三个页签都要点开原件，弹层只留这一处：各自挂一份，屏幕上会出现三个预览窗。
 */
const {
  visible: rawVisible, url: rawUrl, name: rawName, open: openRawFile,
} = useRawFile()

function openRaw(row) {
  openRawFile(() => projectApi.fileRaw(p.value.id, row.id), row.filename,
    () => projectApi.fileOpen(p.value.id, row.id))
}

/* ------------------------------------------------------------ 材料上传 */
const uploadShow = ref(false)

function onMaterialUploaded() {
  load()
}

/* ------------------------------------------------------------ 改派承办人
 * 承办人成为真正办理权后，原承办人停用/转岗要有收回途径。
 * 用户列表按当前环节岗位过滤。 */
const reassignShow = ref(false)
const reassignUsers = ref([])
const reassignPick = ref(null)
const reassignLoading = ref(false)

async function openReassign() {
  reassignPick.value = p.value?.handler || null
  reassignLoading.value = true
  try {
    const { data } = await userApi.list({ page_size: 200 })
    const rows = data?.results || data?.items || data || []
    const scope = p.value?.handler_role || ''
    reassignUsers.value = scope
      ? rows.filter((u) => u.is_active && u.role === scope) : []
  } catch (e) {
    showError(e?.response?.data?.detail || '加载用户失败')
  } finally {
    reassignLoading.value = false
    reassignShow.value = true
  }
}

async function doReassign() {
  try {
    await projectApi.reassign(p.value.id, reassignPick.value || null)
    reassignShow.value = false
    ElMessage.success('承办人已更新')
    await load()
    ui.refreshPending()
  } catch (e) {
    showError(e?.response?.data?.detail || '改派失败')
  }
}

/* ------------------------------------------------------------ 修改申报信息 */
const orgGroups = ref([])
const editShow = ref(false)
const saving = ref(false)
const editForm = reactive({
  name: '', unit: '', year: null, category: '',
  contact_name: '', contact_phone: '',
  investment: null, duration_months: null, summary: '', necessity: '', basis: '',
  sensitive: false,
})

async function loadOrgUnits() {
  try {
    const { data } = await orgApi.units()
    orgGroups.value = data.groups || []
  } catch (e) {
    orgGroups.value = []
  }
}

const CATEGORIES = [
  '自然资源', '政务服务', '医疗保障', '教育', '公安政法', '交通运输',
  '生态环境', '城市管理', '农业农村', '文化旅游', '应急管理', '其他',
]

function openEdit() {
  Object.assign(editForm, {
    name: p.value.name || '', unit: p.value.unit || '', year: p.value.year,
    category: p.value.category || '',
    contact_name: p.value.contact_name || '', contact_phone: p.value.contact_phone || '',
    investment: p.value.investment,
    duration_months: p.value.duration_months, summary: p.value.summary || '',
    necessity: p.value.necessity || '', basis: p.value.basis || '',
    sensitive: p.value.sensitive,
  })
  editShow.value = true
}

async function doSaveEdit() {
  if ((editForm.name || '').trim().length < 4) {
    ElMessage.warning('项目名称至少 4 个字')
    return
  }
  if (!editForm.unit) {
    ElMessage.warning('请选择申报单位')
    return
  }
  if (!editForm.contact_name.trim()) {
    ElMessage.warning('请填写联系人')
    return
  }
  const phone = editForm.contact_phone.trim()
  if (!/^1[3-9]\d{9}$/.test(phone)) {
    ElMessage.warning('联系人手机号要填 11 位手机号：这一栏用来发短信通知，座机收不到')
    return
  }
  saving.value = true
  try {
    const { data } = await projectApi.update(p.value.id, { ...editForm })
    p.value = data
    editShow.value = false
    ElMessage.success('已保存')
  } finally {
    saving.value = false
  }
}

/* ------------------------------------------------------------ 删除项目 */
async function removeProject() {
  const isDraft = p.value.stage === 'draft'
  try {
    if (isDraft) {
      await ElMessageBox.confirm('删除后不可恢复（含材料与流转记录）。确认删除？', '删除项目', {
        confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
      })
    } else {
      await ElMessageBox.prompt(
        `该项目当前阶段是「${stageLabel(p.value.stage)}」。删除前系统会把流转留痕与材料原件归档到 data/deleted/，归档失败则不会删除。请输入项目编号 ${p.value.code} 确认。`,
        '删除已进入流程的项目',
        {
          type: 'warning',
          confirmButtonText: '确认删除',
          cancelButtonText: '取消',
          inputPlaceholder: p.value.code,
          inputValidator: (v) => (v || '').trim() === p.value.code || `请完整输入 ${p.value.code}`,
        },
      )
    }
  } catch { return }
  try {
    const { data } = await projectApi.remove(p.value.id)
    ElMessage.success(data?.detail || '已删除')
    router.push({ name: 'projects' })
  } catch { /* 错误提示由拦截器统一弹出 */ }
}

/* ------------------------------------------------------------ 加载 */
async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const { data } = await projectApi.detail(route.params.id)
    p.value = data
    markTodoSeen()
    /* 从「在办箱」点进来会带上 ?action=xxx：直接把那张表摊开，
       不用在一排按钮里找回刚才填的是哪个 */
    const autoKey = `${route.params.id}:${route.query.action}`
    if (autoOpenedKey !== autoKey && route.query.action) {
      const a = (data.actions || []).find((x) => x.key === route.query.action)
      if (a) {
        autoOpenedKey = autoKey
        openAction(a)
      }
    }
  } catch (err) {
    // 项目被删或编号不存在时原来是一片空白，看不出是「没权限」还是「已删除」
    loadError.value = err?.response?.status === 404
      ? '项目不存在，可能已被删除'
      : '项目加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  loadOrgUnits()
})

/* 同一路由换 id（详情页互相跳、或直接改地址栏里的编号）时，Vue 会复用组件，
 * 不重新挂载 —— 不监听的话页面上还是上一个件的数据，看着像这个件办过那些事 */
watch(() => route.params.id, (id, old) => {
  if (!old || !id || id === old) return
  load()
})

/**
 * 进详情即记一条「已读」：侧栏待办角标减一。
 * 项目不是我的待办时后端会拒，这里当作正常情况忽略。
 */
async function markTodoSeen() {
  if (!p.value) return
  try {
    await projectApi.todoAck(p.value.id)
    await ui.refreshPending()
  } catch { /* 不是我的待办，或已经记过 */ }
}
</script>

<template>
  <div v-loading="loading">
    <div v-if="loadError" class="load-error">
      <el-empty :description="loadError" :image-size="72" />
    </div>
    <div v-else-if="p" class="wrap">
      <ProjectHero
        :p="p"
        :view-only="viewOnly"
        :terminal="terminal"
        :can-edit="canEdit"
        :can-export="canExport"
        :can-delete="canDelete"
        :exporting="exporting"
        :missing-required="missingRequired"
        @edit="openEdit"
        @export="exportArchive"
        @delete="removeProject"
        @unlock="unlockAdmin"
      />

      <!-- 页签：一屏只放一类信息。一个项目有五类事（办理、概况、材料、结论、流转），
           全铺在一页要拉两米长，收进页签后每屏只剩一件事 -->
      <div class="card tabs-card">
        <div class="tabs-bar">
          <button
            v-for="t in tabs"
            :key="t.key"
            class="tab-btn"
            :class="{ on: ptab === t.key }"
            @click="ptab = t.key"
          >
            {{ t.label }}<span v-if="t.count" class="tab-count">{{ t.count }}</span>
          </button>
          <!-- 详情页一环节一条，想看每一笔操作（含全部经办意见）才进这个页 -->
          <span v-if="ptab === 'flow'" class="tabs-right">
            <el-tooltip placement="top" popper-class="stat-tip"
              content="进入完整流转页：每个动作、每条经办意见单独一条，可按阶段流转/经办意见筛选">
              <el-button link type="primary" @click="goFlow">逐条流水</el-button>
            </el-tooltip>
          </span>
        </div>

        <!-- ① 本环节办理：办完再看别的 -->
        <TabAct
          v-show="ptab === 'act'"
          :p="p"
          :view-only="viewOnly"
          :actions="actions"
          :action-block-reason="actionBlockReason"
          :can-upload="canUpload"
          :upload-label="uploadLabel"
          :full-archive="fullArchive"
          @open-action="openAction"
          @open-upload="uploadShow = true"
          @open-reassign="openReassign"
          @open-raw="openRaw"
          @refresh="load"
          @resume-draft="resumeDraft"
        />

        <!-- ② 项目概况 -->
        <TabOverview v-show="ptab === 'info'" :p="p" :view-only="viewOnly" :terminal="terminal" />

        <!-- ③ 项目资料 -->
        <TabMaterials
          v-show="ptab === 'files'"
          :p="p"
          :can-upload="canUpload"
          :upload-label="uploadLabel"
          @open-raw="openRaw"
          @open-upload="uploadShow = true"
        />

        <!-- ④ 审核结论 -->
        <TabConclusion
          v-show="ptab === 'concl'"
          :p="p"
          :full-archive="fullArchive"
          @refresh="load"
        />

        <!-- ⑤ 流转记录：一个环节一条，退回补正单列 -->
        <TabFlow v-show="ptab === 'flow'" :p="p" @go-flow="goFlow" />
      </div>
    </div>

    <!-- 修改申报信息 -->
    <el-dialog v-model="editShow" title="修改申报信息" width="720px" top="6vh" :close-on-click-modal="false">
      <ProjectFields
        v-model:form="editForm"
        :groups="orgGroups"
        :categories="CATEGORIES"
      />
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="申报单位必须从市本级单位名录中选择；已提交预审的项目不能改，请走补正流程。"
      />
      <template #footer>
        <el-button @click="editShow = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSaveEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 原件预览 -->
    <RawPreview v-model="rawVisible" :url="rawUrl" :filename="rawName" />

    <!-- 办理动作（表单、草稿、转出选人都在这个构件里） -->
    <ActionDialog v-if="p" ref="actionDlg" :p="p" @refresh="load" />

    <!-- 管理员改派承办人：承办人是真正办理权后的收回/接手入口 -->
    <el-dialog v-model="reassignShow" title="改派承办人" width="480px" :close-on-click-modal="false">
      <p class="ra-hint">当前环节：{{ p?.handler_role }}。改派只换办理人，不改变阶段与 SLA 停留天数。</p>
      <el-select v-model="reassignPick" placeholder="选择新承办人（可清空后收回岗位池）"
                 clearable filterable style="width:100%" :loading="reassignLoading">
        <el-option v-for="u in reassignUsers" :key="u.id" :label="`${u.display}（${u.role_label}）`"
                   :value="u.id" />
      </el-select>
      <p class="ra-hint">选择「清空」表示收回岗位池，该岗位任何人可领取办理。</p>
      <template #footer>
        <el-button @click="reassignShow = false">取消</el-button>
        <el-button type="primary" @click="doReassign">确认改派</el-button>
      </template>
    </el-dialog>

    <!-- 上传材料：就地弹框，不跳页面。项目资料只在本页这一处看和传 -->
    <MaterialUploadDialog
      v-model="uploadShow"
      :project-id="p?.id"
      :fill-hints="['draft', 'supplement'].includes(p?.stage || '')"
      @uploaded="onMaterialUploaded"
    />
  </div>
</template>

<style scoped>
@import './project-detail/base.css';

/* 外壳：整页留白、页签栏、加载失败态。
 * 五个页签的内容各自在自己的构件里，这里只管「一张卡装五个页签」这件事。
 * 整页左右留白只认 --pad-x 一个变量：信息带、页签栏、概况表、正文段落
 * 都从同一条竖线起笔，按窗口宽度分三档收窄。 */

/* 整页左右留白只认一个变量：顶部信息带、页签栏、概况表、正文段落、动作按钮
   都从同一条竖线起笔。原来信息带 24px、页签与内容 52px，两条线差一截，
   宽屏上 52px 相对 1500 多宽的卡片只有 3%，文字看起来就是贴着边框。
   2026-11-23 统一提到 76px，并按窗口宽度分三档收窄。 */
.wrap {
  padding-bottom: 48px;
  --pad-x: 76px;
}

@media (max-width: 1440px) {
  .wrap {
    --pad-x: 56px;
  }
}

@media (max-width: 1220px) {
  .wrap {
    --pad-x: 40px;
  }
}

.load-error {
  padding: 72px 0;
}


/* ------------------------------------------------------------ 页签
 * 一张白卡装五类信息：页签行像公文目录，选中一条蓝线，后面跟该项的条数。
 * 竖着排五类内容要拉两米，横着分页签每屏只剩一件事。 */
/* 白底（2026-11-23 撤回蓝底试版）：内容卡回到 --panel 纯白、边框回到 --line。
   页面底色仍是加深后的 --bg（#E9EFF7），白卡才不会和底糊成一片。 */
.tabs-card {
  margin-top: 16px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
}

.tabs-bar {
  display: flex;
  align-items: center;
  gap: 24px;
  /* 页签文字离卡片边框留够一截：原来上边 12px、左边 22px，字贴着框线，
     点起来像在抠边角。左右与各页签内容统一到 --pad-x，一眼看得出边界 */
  padding: 26px var(--pad-x) 0;
  border-bottom: 1px solid var(--line-soft);
  /* 右栏窄到四百多时四个页签一行放不下，宁可横向滑动，也不要「项目概况」
     被折成「项目/概况」两行 */
  overflow-x: auto;
  scrollbar-width: none;
}

.tabs-bar::-webkit-scrollbar { display: none; }

.tab-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  white-space: nowrap;
  gap: 6px;
  padding: 8px 4px 13px;
  border: 0;
  background: none;
  font-family: inherit;
  font-size: 14px;
  color: var(--ink-3);
  cursor: pointer;
  transition: color 0.12s;
}

.tab-btn:hover {
  color: var(--ink);
}

.tab-btn.on {
  color: var(--brand-deep);
  font-weight: 600;
}

.tab-btn.on::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  border-radius: 2px 2px 0 0;
  background: var(--brand);
}

.tab-count {
  min-width: 18px;
  height: 17px;
  padding: 0 5px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  background: var(--panel-3);
  color: var(--ink-3);
  font-size: 12px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}

.tab-btn.on .tab-count {
  background: var(--brand-bg);
  color: var(--brand-deep);
}

.tabs-right {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
}


/* 页签文字、办理卡标题、概况表左边缘落在同一条线上（--pad-x）。
   上下也留足：内容顶到分隔线、正文压着卡片下沿，都会显得页面被裁掉一块 */
.tab-pane {
  padding: 32px var(--pad-x) 64px;
}

/* 项目概况是裸内容直接铺在卡上（概况表 + 三段长正文），别的页签外面还裹着一层
   带内边距的内嵌卡，所以同样的 32/64 落到字上就短了一截：
   实测首行距页签分隔线 45、末行距卡底 65，审核结论是 48 / 76。
   这里单独补齐，四个页签的上下留白看起来才是一条规矩。 */
.tab-pane.pane-info {
  padding-top: 35px;
  padding-bottom: 75px;
}

/* 办理页签曾经把卡内边距清零，再靠 10px 补回来 —— 结果动作按钮离卡片下沿
   只剩十来个像素，字和按钮都像贴在边框上。这里只清横向，纵向交给卡内自己 */
.tab-cols {
  padding: 22px 0 44px;
}

.tab-cols :deep(.el-card) {
  padding: 0 var(--pad-x);
}

/* 页签里不再叠「卡中卡」：内容本来就是一整块，套一层带边框的卡片
   等于给盒子再包一层盒子，页签的分隔线已经说明这是一组 */
.tabs-card :deep(.el-card) {
  border: 0;
  box-shadow: none;
  background: transparent;
}

.tabs-card :deep(.el-card__header) {
  padding: 0 0 10px;
  border-bottom: 0;
}

.tabs-card :deep(.el-card__body) {
  padding: 0;
}

.tab-pane :deep(.el-card + .el-card) {
  margin-top: 6px;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}


/* 流转记录入口（右栏） */
/* 右栏：办理信息里挂一行流转记录，两张卡并成一张 */
.ra-hint {
  font-size: 12px;
  color: var(--ink-4);
  line-height: 1.7;
  margin: 0 0 10px;
}

.ra-hint:last-of-type {
  margin: 8px 0 0;
}
</style>
