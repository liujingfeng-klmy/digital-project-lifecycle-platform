<script setup>
/**
 * 新建申报。
 *
 * 单独抽成一个组件：原来它长在「已办项目」页上 —— 一页叫「已办」，
 * 右上角却摆着「新建申报」，人要新建得先点进已办项目去找。这个入口
 * 现在挂在待办工作台（那里才是「我手上要做的事」）。
 *
 * 两种落法：
 *  - 只建单（saveLater）→ 件留在待办箱，回头接着填；
 *  - 建完直接进详情页传可研。
 * 两种都在「申报中」，提交前都能改，区别只在建完往哪走。
 */
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { orgApi, projectApi } from '@/api'
import { downloadBlob } from '@/utils/fileview'
import { useUserStore } from '@/stores/user'
import ProjectFields from '@/components/ProjectFields.vue'

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'created'])

const router = useRouter()
const userStore = useUserStore()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const creating = ref(false)
const form = reactive({
  name: '', unit: '', year: new Date().getFullYear(), category: '',
  contact_name: '', contact_phone: '',
  investment: null, duration_months: null, summary: '', necessity: '', basis: '',
  sensitive: false,
})

const CATEGORIES = [
  '自然资源', '政务服务', '医疗保障', '教育', '公安政法', '交通运输',
  '生态环境', '城市管理', '农业农村', '文化旅游', '应急管理', '其他',
]

/* ------------------------------------------------------------ 单位名录 */
const orgGroups = ref([])

async function loadOrgUnits() {
  try {
    const { data } = await orgApi.units()
    orgGroups.value = data.groups || []
    // 名录回来得比打开对话框晚时补上默认单位（表单此时还是空的）
    if (visible.value && !form.unit) form.unit = defaultUnit.value
  } catch {
    orgGroups.value = []
  }
}
loadOrgUnits()

/** 账号单位若在名录里，新建时先选上；材料解析出别的单位会覆盖它 */
const defaultUnit = computed(() => {
  const u = userStore.user?.unit
  if (!u) return ''
  for (const g of orgGroups.value) {
    if (g.items.some((it) => it.name === u)) return u
  }
  return ''
})

/* ------------------------------------------------------------ 材料预解析 */
// 材料预解析状态：idle 未传 / parsing 解析中 / done 已解析 / error 失败
const prefillState = ref('idle')
const prefillData = ref(null)
const prefillPct = ref(0)
const prefillName = ref('')
const prefillError = ref('')
// 已经解析过至少一次：之后再传材料只补空字段，不覆盖用户手改过的值
const hasPrefilled = ref(false)

function resetPrefill() {
  prefillState.value = 'idle'
  prefillData.value = null
  prefillPct.value = 0
  prefillName.value = ''
  prefillError.value = ''
  hasPrefilled.value = false
}

function reset() {
  Object.assign(form, {
    name: '', unit: defaultUnit.value, year: new Date().getFullYear(), category: '',
    // 联系人默认取账号本人，手机号有就带出来 —— 多数情况就是申报人自己
    contact_name: userStore.user?.display || userStore.user?.name || '',
    contact_phone: userStore.user?.phone || '',
    investment: null, duration_months: null,
    summary: '', necessity: '', basis: '', sensitive: false,
  })
  resetPrefill()
}

function open() {
  reset()
  visible.value = true
}

// 父组件只要把 v-model 置 true 就是「开一张新单」：每次打开都从空白开始，
// 上一次没提交的内容不该悄悄留在表单里
watch(() => props.modelValue, (v) => { if (v) reset() })

const SOURCE_LABEL = {
  name: '项目名称', unit: '申报单位', investment: '总投资',
  year: '申报年度', duration_months: '建设周期', summary: '建设内容',
  necessity: '建设必要性', basis: '政策依据', category: '业务领域',
}

/** 把解析结果填进表单。首次解析覆盖，重新解析只补空 —— 手改过的值不能悄悄被冲掉 */
function applyPrefill(data, force) {
  const f = data.fields || {}
  for (const [k, v] of Object.entries(f)) {
    if (!(k in form)) continue
    if (v === null || v === '' || v === undefined) continue
    const cur = form[k]
    const empty = cur === null || cur === undefined || cur === ''
    if (force || empty) form[k] = v
  }
}

async function onPickMaterial(uploadFile) {
  const raw = uploadFile?.raw
  if (!raw) return
  prefillName.value = raw.name
  prefillState.value = 'parsing'
  prefillPct.value = 0
  prefillError.value = ''
  const first = !hasPrefilled.value
  const fd = new FormData()
  fd.append('file', raw)
  try {
    const { data } = await projectApi.prefill(fd, (v) => { prefillPct.value = v })
    prefillData.value = data
    applyPrefill(data, first)
    hasPrefilled.value = true
    prefillState.value = 'done'
  } catch (e) {
    prefillData.value = null
    prefillState.value = 'error'
    prefillError.value = e?.response?.data?.detail || e?.message || '解析失败'
  }
}

const filledLabels = computed(() => {
  const f = prefillData.value?.fields || {}
  return Object.keys(f).filter((k) => SOURCE_LABEL[k]).map((k) => SOURCE_LABEL[k])
})

async function downloadTemplate() {
  try {
    const { data } = await projectApi.template()
    downloadBlob(new Blob([data]), '可行性研究报告模板.docx')
  } catch {
    ElMessage.error('模板下载失败，请稍后重试')
  }
}

/* ------------------------------------------------------------ 建单 */
async function doCreate(saveLater = false) {
  if (form.name.trim().length < 4) {
    ElMessage.warning('项目名称至少 4 个字')
    return
  }
  if (!form.unit) {
    ElMessage.warning('请选择申报单位')
    return
  }
  if (!form.contact_name.trim()) {
    ElMessage.warning('请填写联系人')
    return
  }
  const phone = form.contact_phone.trim()
  if (!/^1[3-9]\d{9}$/.test(phone)) {
    ElMessage.warning('联系人手机号要填 11 位手机号：这一栏用来发短信通知，座机收不到')
    return
  }
  creating.value = true
  try {
    const payload = { ...form }
    if (prefillData.value?.token) payload.prefill_token = prefillData.value.token
    const { data } = await projectApi.create(payload)
    const attach = data.attach
    if (attach?.attached) {
      ElMessage.success(`已创建 ${data.code}，${attach.message}`)
    } else if (attach?.message) {
      ElMessage.warning(`已创建 ${data.code}，但材料没挂上：${attach.message}`)
    } else {
      ElMessage.success(`已创建 ${data.code}，接着上传可研报告`)
    }
    visible.value = false
    if (saveLater) {
      ElMessage.success(`${data.code} 已存入待办箱，随时回来接着填`)
      emit('created', { project: data, saveLater: true })
      return
    }
    emit('created', { project: data, saveLater: false })
    router.push({ name: 'project-detail', params: { id: data.id }, query: { from: 'todo' } })
  } finally {
    creating.value = false
  }
}

defineExpose({ open })
</script>

<template>
  <el-dialog
    v-model="visible"
    title="新建申报"
    width="720px"
    top="6vh"
    :close-on-click-modal="false"
  >
    <div class="mat-box">
      <div class="mat-head">
        <span class="mat-title">第一步 · 上传可研报告或项目建设方案</span>
        <el-button link type="primary" size="small" @click="downloadTemplate">
          下载可研报告模板
        </el-button>
      </div>
      <div class="mat-sub">
        上传后自动识别名称、单位、投资等；识别不出的在下方手工补。
      </div>
      <el-upload
        drag
        :auto-upload="false"
        :show-file-list="false"
        :limit="1"
        accept=".pdf,.docx,.txt"
        :on-change="onPickMaterial"
      >
        <div class="up-inner">
          <div class="up-main">{{ prefillName || '点击或拖拽文件到这里' }}</div>
          <div class="up-sub">PDF / DOCX / TXT，单份 200MB 内；也可以跳过，直接手工填</div>
        </div>
      </el-upload>

      <el-progress
        v-if="prefillState === 'parsing'"
        :percentage="prefillPct"
        :stroke-width="5"
        :text-inside="true"
        style="margin-top: 10px"
      />
      <el-alert
        v-if="prefillState === 'error'"
        type="error"
        :closable="false"
        show-icon
        :title="prefillError"
        style="margin-top: 10px"
      />

      <template v-if="prefillState === 'done'">
        <el-alert
          type="success"
          :closable="false"
          show-icon
          :title="`已识别 ${filledLabels.length} 项：${filledLabels.join('、') || '无'}`"
          :description="prefillData?.ai_fields?.length
            ? '其中橙标字段是模型根据材料概括的，不是原文摘录，请核对后再提交。'
            : ''"
          style="margin-top: 10px"
        />
        <el-alert
          v-if="prefillData?.unit_unmatched"
          type="warning"
          :closable="false"
          show-icon
          :title="`材料里的单位「${prefillData.unit_raw}」不在市本级单位名录内，请在下方重新选择`"
          style="margin-top: 8px"
        />
        <div v-if="prefillData?.missing?.length" class="miss">
          <div class="miss-title">
            以下 {{ prefillData.missing.length }} 项没从材料里识别出来：
          </div>
          <ul>
            <li v-for="m in prefillData.missing" :key="m.key">
              <b>{{ m.label }}</b>：写在{{ m.hint }}
            </li>
          </ul>
          <div class="miss-act">
            <span>材料没写这部分，评审也看不到。建议按模板补齐后重新上传。</span>
            <el-button size="small" @click="downloadTemplate">下载模板</el-button>
          </div>
        </div>
      </template>
    </div>

    <el-divider content-position="left">第二步 · 核对申报信息</el-divider>

    <ProjectFields
      v-model:form="form"
      :groups="orgGroups"
      :sources="prefillData?.sources || {}"
      :categories="CATEGORIES"
    />

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="创建后可继续修改；提交预审后材料锁定，只能走补正。"
    />
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button :loading="creating" @click="doCreate(true)">保存，稍后继续</el-button>
      <el-button type="primary" :loading="creating" @click="doCreate(false)">创建申报</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.mat-box {
  background: var(--panel-2); border: 1px solid var(--line-soft);
  border-radius: 9px; padding: 12px 14px;
}
.mat-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.mat-title { font-size: 13px; color: var(--ink); font-weight: 500; }
.mat-sub { font-size: 11.5px; color: var(--ink-3); line-height: 1.7; margin-bottom: 10px; }
.up-inner { padding: 6px 0; }
.up-main { font-size: 13px; color: var(--ink-2); }
.up-sub { font-size: 11.5px; color: var(--ink-4); margin-top: 4px; }
.miss {
  margin-top: 10px; padding: 10px 12px; border-radius: 7px;
  background: var(--warn-bg); border: 1px solid #F0DCBF;
}
.miss-title { font-size: 12px; color: var(--warn); font-weight: 500; }
.miss ul { margin: 6px 0 0; padding-left: 18px; }
.miss li { font-size: 11.5px; color: var(--ink-2); line-height: 1.9; }
.miss-act {
  display: flex; align-items: center; justify-content: space-between;
  gap: 10px; margin-top: 8px; font-size: 11.5px; color: var(--ink-3);
}
</style>
