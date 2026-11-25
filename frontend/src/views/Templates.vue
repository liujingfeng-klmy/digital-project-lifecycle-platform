<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { projectApi, templateApi } from '@/api'
import { showError } from '@/api/http'
import { downloadBlob } from '@/utils/fileview'

const loading = ref(true)
const saving = ref(false)
const rows = ref([])
const stages = ref([])

const KIND_OPTIONS = [
  { value: 'precheck_notice', label: '预审修正说明' },
  { value: 'cost_review_task', label: '费用评审任务书' },
  { value: 'approval_reply', label: '立项批复' },
  { value: 'data_collect', label: '数据归集证明' },
  { value: 'other', label: '其他材料模板' },
]

// 占位符放在数据里逐个渲染：直接写在模板里会和 Vue 的 {{ }} 语法冲突
const TOKENS = [
  '{{project_name}}', '{{unit}}', '{{investment}}', '{{duration_months}}',
  '{{project_code}}', '{{year}}', '{{date}}', '{{deadline}}', '{{doc_no}}',
  '{{entrust_unit}}', '{{entrust_contact}}', '{{entrust_phone}}', '{{days}}',
  '{{trial_summary}}', '{{data_collect}}', '{{issuer}}',
]

const show = ref(false)
const editing = ref(null)
const fileRaw = ref(null)
const form = reactive({
  name: '',
  kind: 'other',
  stage: '',
  description: '',
  active: true,
})

const kindLabel = (v) => KIND_OPTIONS.find((k) => k.value === v)?.label || v

async function load() {
  loading.value = true
  try {
    const [t, s] = await Promise.all([templateApi.list(), projectApi.stages()])
    const data = t.data
    rows.value = Array.isArray(data) ? data : (data.results || [])
    stages.value = s.data.stages || []
  } finally {
    loading.value = false
  }
}

onMounted(load)

function openCreate() {
  editing.value = null
  fileRaw.value = null
  Object.assign(form, {
    name: '', kind: 'other', stage: '', description: '', active: true,
  })
  show.value = true
}

function openEdit(row) {
  editing.value = row
  fileRaw.value = null
  Object.assign(form, {
    name: row.name,
    kind: row.kind,
    stage: row.stage || '',
    description: row.description || '',
    active: row.active,
  })
  show.value = true
}

function pickFile(file) {
  fileRaw.value = file?.raw || null
}

function buildFormData() {
  const fd = new FormData()
  fd.append('name', form.name.trim())
  fd.append('kind', form.kind)
  fd.append('stage', form.stage || '')
  fd.append('description', form.description || '')
  fd.append('active', form.active ? 'true' : 'false')
  if (fileRaw.value) fd.append('file', fileRaw.value)
  return fd
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写模板名称')
    return
  }
  if (form.kind === 'other' && !fileRaw.value && !editing.value?.filename) {
    ElMessage.warning('「其他材料模板」必须上传模板文件')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await templateApi.update(editing.value.id, buildFormData())
      ElMessage.success('已保存')
    } else {
      await templateApi.create(buildFormData())
      ElMessage.success('已新增')
    }
    show.value = false
    await load()
  } catch (e) {
    showError(e?.response?.data?.detail || '保存失败，请检查文件格式（docx）')
  } finally {
    saving.value = false
  }
}

async function download(row) {
  try {
    const { data } = await templateApi.file(row.id)
    downloadBlob(data, row.filename || `${row.name}.docx`)
  } catch {
    ElMessage.error('下载失败，模板文件可能已丢失')
  }
}

async function toggle(row) {
  const fd = new FormData()
  fd.append('active', row.active ? 'false' : 'true')
  try {
    await templateApi.update(row.id, fd)
    row.active = !row.active
    ElMessage.success(row.active ? '已启用' : '已停用')
  } catch {
    ElMessage.error('操作失败')
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(
      `删除《${row.name}》？删除后系统生成文书时会改用内置模板。`,
      '删除模板', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  try {
    await templateApi.remove(row.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    ElMessage.error('删除失败')
  }
}

const stageOptions = computed(() =>
  stages.value.filter((s) => !['reserve', 'rejected', 'accepted'].includes(s.key)),
)
</script>

<template>
  <div class="tpl">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="tip"
      title="按「文书类型」取启用中的模板生成文书；「适用环节」只用于归类。"
    >
      <template #default>
        正文可用占位符：
        <code v-for="t in TOKENS" :key="t">{{ t }}</code>，
        生成时自动替换。《预审修正说明》需保留「一、二、三…」编号填空行。
      </template>
    </el-alert>

    <el-card shadow="never" class="card">
      <template #header>
        <div class="head">
          <div>
            <span class="card-title">文书模板</span>
            <span class="card-sub">共 {{ rows.length }} 份</span>
          </div>
          <el-button type="primary" @click="openCreate">新增模板</el-button>
        </div>
      </template>

      <el-table v-loading="loading" :data="rows" size="small" class="table">
        <el-table-column prop="name" label="模板名称" min-width="190" />
        <el-table-column label="文书类型" width="140">
          <template #default="{ row }">{{ kindLabel(row.kind) }}</template>
        </el-table-column>
        <el-table-column label="适用环节" width="120">
          <template #default="{ row }">
            <span class="stage-chip">{{ row.stage_label || '通用' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="文件" min-width="170">
          <template #default="{ row }">
            <span v-if="row.filename" class="mono file">{{ row.filename }}</span>
            <span v-else class="muted">内置模板（未上传替换件）</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.active ? 'success' : 'info'" size="small" effect="plain">
              {{ row.active ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="download(row)">下载</el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link size="small" @click="toggle(row)">
              {{ row.active ? '停用' : '启用' }}
            </el-button>
            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="show"
      :title="editing ? '编辑模板' : '新增模板'"
      width="560px"
      :close-on-click-modal="false"
    >
      <el-form label-width="96px" label-position="left">
        <el-form-item label="模板名称" required>
          <el-input v-model="form.name" maxlength="120" placeholder="例如：费用评审任务书模板" />
        </el-form-item>
        <el-form-item label="文书类型" required>
          <el-select v-model="form.kind" style="width: 100%">
            <el-option v-for="k in KIND_OPTIONS" :key="k.value" :label="k.label" :value="k.value" />
          </el-select>
          
        </el-form-item>
        <el-form-item label="适用环节">
          <el-select v-model="form.stage" clearable placeholder="通用（不绑定环节）" style="width: 100%">
            <el-option v-for="s in stageOptions" :key="s.key" :label="s.label" :value="s.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" maxlength="200" />
        </el-form-item>
        <el-form-item label="模板文件">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept=".docx"
            :on-change="pickFile"
          >
            <el-button>选择 docx 文件</el-button>
          </el-upload>
          <span v-if="fileRaw" class="mono picked">{{ fileRaw.name }}</span>
          <span v-else-if="editing?.filename" class="muted">
            当前：{{ editing.filename }}（不选则保留）
          </span>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="show = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.tip { margin-bottom: 14px; }
.tip code {
  font-family: 'JetBrains Mono', monospace;
  background: var(--panel-3);
  padding: 1px 5px;
  border-radius: 4px;
  margin: 0 2px;
  font-variant-ligatures: none;
}
.card { border-radius: var(--radius-lg); }
.head { display: flex; align-items: center; justify-content: space-between; }
.card-title { font-size: 15px; font-weight: 600; }
.card-sub { font-size: 11.5px; color: var(--ink-4); margin-left: 8px; }
.table { width: 100%; }
.file { font-size: 11.5px; color: var(--ink-3); }
.stage-chip {
  font-size: 12px;
  padding: 2px 7px;
  border-radius: 5px;
  background: var(--brand-bg);
  color: var(--brand-deep);
}
.hint { font-size: 12px; color: var(--ink-4); line-height: 1.5; margin-top: 4px; }
.picked { font-size: 12px; margin-left: 10px; color: var(--ink-2); }
.mono { font-family: 'JetBrains Mono', monospace; }
</style>
