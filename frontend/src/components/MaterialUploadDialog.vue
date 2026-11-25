<script setup>
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { projectApi } from '@/api'
import { downloadBlob } from '@/utils/fileview'

/* 上传材料只有一套流程：选类型 → 选文件 → 上传并解析 → 说清解析结果。
   项目详情页的「项目资料」页签用的就是这一份 —— 谁点上传都在原地弹框，
   不靠跳页面来复用对话框（补正时人正在办理页，跳走就丢了办理上下文）。 */

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  projectId: { type: [Number, String], default: null },
  /** 补正阶段不许改申报信息，回填与缺项提示就不弹，免得指到点不动的按钮上 */
  fillHints: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'uploaded'])
const router = useRouter()

const DOC_TYPES = [
  { value: 'feasibility', label: '可行性研究报告' },
  { value: 'proposal', label: '项目建议书' },
  { value: 'budget', label: '投资概算' },
  { value: 'approval', label: '批复或依据文件' },
  { value: 'evidence', label: '异议证据' },
  { value: 'other', label: '其他材料' },
]

const type = ref('feasibility')
const list = ref([])
const pct = ref(0)
const busy = ref(false)

watch(() => props.modelValue, (open) => {
  if (!open) return
  type.value = 'feasibility'
  list.value = []
  pct.value = 0
})

function close() {
  emit('update:modelValue', false)
}

async function submit() {
  const files = list.value.map((f) => f.raw).filter(Boolean)
  if (!files.length) {
    ElMessage.warning('请先选择文件')
    return
  }
  busy.value = true
  pct.value = 0
  try {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    fd.append('doc_type', type.value)
    const { data } = await projectApi.upload(props.projectId, fd, (v) => { pct.value = v })
    const ok = data.results.filter((r) => r.ok).length
    const bad = data.results.filter((r) => !r.ok)
    if (bad.length) ElMessage.warning(`${ok} 份解析成功，${bad.length} 份失败：${bad[0].message}`)
    else ElMessage.success(`${ok} 份材料已解析`)
    close()
    emit('uploaded', data)

    if (!props.fillHints) return
    // 回填了哪些、还缺哪些，当场说清楚 —— 否则用户不知道「解析完成」之后
    // 申报表里其实已经有值了，或者为什么提交时被拦
    const filled = data.filled || []
    if (filled.length) {
      ElMessage.info(`已从材料回填：${filled.map((f) => f.label).join('、')}，请到项目详情核对`)
    }
    const missing = data.missing_required || []
    if (missing.length) {
      ElMessageBox.alert(
        `<div style="line-height:1.9;font-size:13px">材料里没能识别出以下必填信息：<br>`
        + missing.map((m) => `· <b>${m.label}</b> —— ${m.hint}`).join('<br>')
        + `<br><br>可以到项目详情点「修改申报信息」手工补填；如果是材料本身缺这部分内容，`
        + `按模板改完材料后重新上传，系统会重新识别。</div>`,
        '材料缺必填信息',
        { dangerouslyUseHTMLString: true, confirmButtonText: '去补填' },
      ).then(() => {
        if (props.projectId) {
          router.push({ name: 'project-detail', params: { id: props.projectId } })
        }
      }).catch(() => {})
    }
  } finally {
    busy.value = false
  }
}

/** 可研报告模板：解析不全时让申报单位照模板改材料。 */
async function downloadTemplate() {
  try {
    const { data } = await projectApi.template()
    downloadBlob(data, '可研报告模板.docx')
  } catch { ElMessage.error('模板下载失败，请稍后重试') }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="上传项目材料"
    width="560px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form label-position="top">
      <el-form-item label="材料类型">
        <el-select v-model="type" style="width: 100%">
          <el-option v-for="d in DOC_TYPES" :key="d.value" :label="d.label" :value="d.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="选择文件（可多选）">
        <el-upload
          v-model:file-list="list"
          drag
          multiple
          :auto-upload="false"
          accept=".pdf,.docx,.txt,.md,.xlsx,.xlsm"
          style="width: 100%"
        >
          <div class="upload-inner">
            <div>把可研报告、建议书、概算、批复拖到这里，或点击选择</div>
            <div class="upload-sub">
              PDF / DOCX / TXT / XLSX，单份 200MB 内；Excel 只存档留底，不参与正文解析
            </div>
          </div>
        </el-upload>
      </el-form-item>
    </el-form>
    <el-progress v-if="busy" :percentage="pct" :stroke-width="6" />
    <div class="up-tip">
      识别出的项目信息会自动填进申报表。
      <el-button link type="primary" size="small" @click="downloadTemplate">下载可研报告模板</el-button>
    </div>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="busy" @click="submit">上传并解析</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.upload-inner { font-size: 12.5px; color: var(--ink-2); line-height: 1.9; }
.upload-sub { font-size: 12px; color: var(--ink-4); }
.up-tip { font-size: 11.5px; color: var(--ink-4); line-height: 1.9; }
</style>
