<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { archiveApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { useRawFile } from '@/composables/useRawFile'
import CompareReport from '@/components/CompareReport.vue'
import RawPreview from '@/components/RawPreview.vue'

const userStore = useUserStore()
const canEdit = computed(() => ['reviewer', 'auditor', 'admin'].includes(userStore.role))
// 申报单位不能看别家单位的历史原文（后端 CanReadArchiveDetail 同一口径）
const canReadRaw = computed(() => userStore.role !== 'applicant')

/* ---------------------------------------------------------------- 列表 */
const loading = ref(false)
const stats = ref(null)
const rows = ref([])
const total = ref(0)
const filters = reactive({ q: '', year: '', status: '', page: 1, size: 20 })

const STATUS_OPTIONS = [
  { value: 'in_use', label: '在用' },
  { value: 'building', label: '建设中' },
  { value: 'accepted', label: '已验收' },
  { value: 'offline', label: '已停用' },
  { value: 'unknown', label: '未知' },
]

async function loadList() {
  loading.value = true
  try {
    const { data } = await archiveApi.list({
      q: filters.q || undefined,
      year: filters.year || undefined,
      status: filters.status || undefined,
      page: filters.page,
      page_size: filters.size,
    })
    rows.value = data.results || []
    total.value = data.count || 0
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  const { data } = await archiveApi.stats()
  stats.value = data
}

function resetPage() {
  filters.page = 1
  loadList()
}

onMounted(() => {
  loadList()
  loadStats()
})

/* ---------------------------------------------------------------- 导入 */
const importShow = ref(false)
const pending = ref([])
const importing = ref(false)
const uploadPct = ref(0)
const results = ref(null)
const importForm = reactive({ project: null, year: null })

function onPick(uploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  const exist = pending.value.some((f) => f.name === raw.name && f.size === raw.size)
  if (!exist) pending.value.push(raw)
}

function removePending(i) {
  pending.value.splice(i, 1)
}

function openImport() {
  pending.value = []
  results.value = null
  uploadPct.value = 0
  importForm.project = null
  importForm.year = null
  importShow.value = true
}

async function doImport() {
  if (!pending.value.length) {
    ElMessage.warning('请先选择文件')
    return
  }
  importing.value = true
  uploadPct.value = 0
  results.value = null
  try {
    const fd = new FormData()
    pending.value.forEach((f) => fd.append('files', f))
    if (importForm.project) fd.append('project', importForm.project)
    if (importForm.year) fd.append('year', importForm.year)
    const { data } = await archiveApi.importFiles(fd, (p) => { uploadPct.value = p })
    results.value = data
    const s = data.summary
    if (s.failed) {
      ElMessage.warning(`导入完成：成功 ${s.ok}、跳过 ${s.skipped}、失败 ${s.failed}`)
    } else {
      ElMessage.success(`导入完成：成功 ${s.ok}、跳过 ${s.skipped}`)
    }
    loadList()
    loadStats()
  } finally {
    importing.value = false
  }
}

const pendingSize = computed(() =>
  pending.value.reduce((n, f) => n + f.size, 0) / 1048576,
)

/* ---------------------------------------------------------------- 详情 */
const detailShow = ref(false)
const detail = ref(null)
const editing = ref(false)
const editForm = reactive({ name: '', unit: '', year: null, investment: null, category: '', status: 'unknown', note: '' })
const openedFile = ref(null)
const fileDetail = ref(null)
const textShow = ref(false)
const fileText = ref({ filename: '', text: '' })

async function openDetail(row) {
  detailShow.value = true
  openedFile.value = null
  fileDetail.value = null
  editing.value = false
  const { data } = await archiveApi.detail(row.id)
  detail.value = data
  Object.assign(editForm, {
    name: data.name, unit: data.unit, year: data.year,
    investment: data.investment, category: data.category,
    status: data.status, note: data.note,
  })
}

async function saveEdit() {
  await archiveApi.update(detail.value.id, { ...editForm })
  ElMessage.success('已保存')
  editing.value = false
  await openDetail(detail.value)
  loadList()
}

async function toggleFile(f) {
  if (openedFile.value === f.id) {
    openedFile.value = null
    fileDetail.value = null
    return
  }
  openedFile.value = f.id
  const { data } = await archiveApi.file(f.id)
  fileDetail.value = data
}

async function showText(f) {
  const { data } = await archiveApi.fileText(f.id)
  fileText.value = { filename: data.filename, text: data.text }
  textShow.value = true
}

/** 打开原件：PDF / 图片在页内预览，Word 之类落成下载。 */
const {
  visible: rawVisible, url: rawUrl, name: rawName, open: openRawFile,
} = useRawFile()

function openRaw(f) {
  openRawFile(() => archiveApi.raw(f.id), f.filename, () => archiveApi.openLocal(f.id))
}

async function reparse(f) {
  try {
    await archiveApi.reparse(f.id)
    ElMessage.success('已重新解析')
    await openDetail(detail.value)
  } catch { /* 已提示 */ }
}

async function removeFile(f) {
  try {
    await ElMessageBox.confirm(`确认删除材料《${f.filename}》？原件文件会一并删除。`, '删除材料', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch { return }
  await archiveApi.removeFile(f.id)
  ElMessage.success('已删除')
  await openDetail(detail.value)
  loadList()
  loadStats()
}

async function removeProject(row) {
  try {
    await ElMessageBox.confirm(`确认删除项目「${row.name}」及其全部材料？此操作不可恢复。`, '删除历史项目', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch { return }
  await archiveApi.remove(row.id)
  ElMessage.success('已删除')
  loadList()
  loadStats()
}

/* ---------------------------------------------------------------- 比对 */
const compareShow = ref(false)
const compareMode = ref('file')
const compareFile = ref(null)
const compareText = reactive({ text: '', name: '' })
const libraryPick = reactive({ project: null, file: null })
const comparing = ref(false)

const reportShow = ref(false)
const report = ref(null)

const fileFns = computed(() => {
  const all = fileDetail.value?.functions || []
  return {
    biz: all.filter((f) => !f.generic),
    gen: all.filter((f) => f.generic),
  }
})

const fileOptions = computed(() => {
  const p = rows.value.find((r) => r.id === libraryPick.project)
  return p ? p.files.filter((f) => f.status === 'ready') : []
})

function openCompare() {
  compareMode.value = 'file'
  compareFile.value = null
  compareText.text = ''
  compareText.name = ''
  libraryPick.project = null
  libraryPick.file = null
  compareShow.value = true
}

function onComparePick(uploadFile) {
  compareFile.value = uploadFile.raw || null
}

async function runCompare() {
  let payload
  if (compareMode.value === 'file') {
    if (!compareFile.value) { ElMessage.warning('请选择要比对的材料'); return }
    payload = new FormData()
    payload.append('file', compareFile.value)
    payload.append('top_n', 8)
    if (compareText.name) payload.append('name', compareText.name)
  } else if (compareMode.value === 'text') {
    if ((compareText.text || '').length < 100) {
      ElMessage.warning('正文太短，至少 100 字才能做有意义的比对')
      return
    }
    payload = { text: compareText.text, name: compareText.name, top_n: 8 }
  } else {
    if (!libraryPick.file) { ElMessage.warning('请选择一份档案材料'); return }
    payload = { file_id: libraryPick.file, top_n: 8 }
  }

  comparing.value = true
  try {
    const { data } = await archiveApi.compare(payload)
    report.value = data
    reportShow.value = true
    compareShow.value = false
  } finally {
    comparing.value = false
  }
}

/* ---------------------------------------------------------------- 核定台账 */
const reviewShow = ref(false)
const reviewRows = ref([])
const reviewFilter = ref('')
const reviewLoading = ref(false)

async function loadReviews() {
  reviewLoading.value = true
  try {
    const { data } = await archiveApi.reviews({
      verdict: reviewFilter.value || undefined,
    })
    reviewRows.value = data.results || data || []
  } catch {
    reviewRows.value = []
  } finally {
    reviewLoading.value = false
  }
}

function openReviews() {
  reviewShow.value = true
  loadReviews()
}

function fmtTime(s) {
  if (!s) return ''
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString('zh-CN', { hour12: false }).slice(0, 16)
}

/* ---------------------------------------------------------------- 年度核对 */
// 年度识别改过一轮：老数据里有一批取的是「正文出现次数最多的年份」，
// 政策依据里的 2019 会把申报年度顶掉。这里只列差异、不自动改 ——
// 有些项目材料是 2023 年编制的、2025 年才立项，按材料年份改反而改错。
const yearShow = ref(false)
const yearLoading = ref(false)
const yearApplying = ref(false)
const yearItems = ref([])
const yearPicked = ref([])

async function openYearFix() {
  yearShow.value = true
  yearLoading.value = true
  yearPicked.value = []
  try {
    const { data } = await archiveApi.yearDiff()
    yearItems.value = data.items || []
  } finally {
    yearLoading.value = false
  }
}

async function applyYears() {
  if (!yearPicked.value.length) {
    ElMessage.warning('请先勾选要应用的项目')
    return
  }
  yearApplying.value = true
  try {
    const { data } = await archiveApi.yearApply(yearPicked.value)
    ElMessage.success(`已修正 ${data.changed} 个项目的年度`)
    yearShow.value = false
    loadList()
    loadStats()
  } catch { /* 错误提示由拦截器统一弹出 */ } finally {
    yearApplying.value = false
  }
}

/* ---------------------------------------------------------------- 杂 */
const years = computed(() => (stats.value?.by_year || []).map((y) => y.year))

function fmtInvest(v) {
  if (v === null || v === undefined || v === '') return '—'
  return Number(v).toLocaleString('zh-CN')
}
</script>

<template>
  <div class="arch">
    <!-- 概览 -->
    <div class="top-row">
      <div class="stat-strip">
        <div class="stat-tile">
          <div class="stat-num">{{ stats?.projects ?? '—' }}</div>
          <div class="stat-label">历史项目</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ stats?.files ?? '—' }}</div>
          <div class="stat-label">材料份数</div>
        </div>
        <div class="stat-tile">
          <div class="stat-num">{{ ((stats?.chars || 0) / 10000).toFixed(1) }}</div>
          <div class="stat-label">入库字数（万字）</div>
        </div>
        <div class="stat-tile" :class="{ alert: stats?.failed }">
          <div class="stat-num">{{ stats?.failed ?? 0 }}</div>
          <div class="stat-label">解析失败</div>
        </div>
      </div>
      <div class="top-actions">
        <el-button v-if="canEdit" type="primary" @click="openImport">导入历史材料</el-button>
        <el-button @click="openCompare">重复建设比对</el-button>
        <el-button @click="openReviews">核定台账</el-button>
        <el-button v-if="canEdit" @click="openYearFix">年度核对</el-button>
      </div>
    </div>

    <!-- 筛选 -->
    <div class="toolbar">
      <el-input
        v-model="filters.q"
        placeholder="项目名称 / 单位 / 文件名"
        clearable
        class="search"
        @keyup.enter="resetPage"
        @clear="resetPage"
      >
        <template #prefix>
          <svg viewBox="0 0 24 24" width="14" height="14" class="search-icon" aria-hidden="true">
            <circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="1.7" />
            <path d="M16 16l4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
          </svg>
        </template>
      </el-input>
      <el-select v-model="filters.year" placeholder="立项年度" clearable class="pick" @change="resetPage">
        <el-option v-for="y in years" :key="y" :label="`${y} 年`" :value="String(y)" />
      </el-select>
      <el-select v-model="filters.status" placeholder="运行状态" clearable class="pick" @change="resetPage">
        <el-option v-for="s in STATUS_OPTIONS" :key="s.value" :label="s.label" :value="s.value" />
      </el-select>
      <el-button @click="resetPage">查询</el-button>
      <span class="hint">历史库只用于判重，不参与流程流转</span>
    </div>

    <!-- 列表 -->
    <div class="table-card">
    <el-table :data="rows" v-loading="loading" size="default" class="table" empty-text="历史档案库还是空的，先导入过往的可研报告">
      <el-table-column label="项目名称" min-width="240">
        <template #default="{ row }">
          <div class="proj-name" @click="openDetail(row)">{{ row.name }}</div>
          <div class="proj-sub">
            {{ row.files?.[0]?.doc_type_label || '未分类' }}
            <span v-if="row.file_count > 1">· 共 {{ row.file_count }} 份材料</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="unit" label="建设单位" min-width="130">
        <template #default="{ row }">{{ row.unit || '—' }}</template>
      </el-table-column>
      <el-table-column prop="year" label="年度" width="72">
        <template #default="{ row }">
          <span>{{ row.year || '—' }}</span>
          <span v-if="row.year_source === 'manual'" class="year-src">人工</span>
        </template>
      </el-table-column>
      <el-table-column label="总投资" width="100" align="right">
        <template #default="{ row }">
          <span v-if="row.investment">{{ fmtInvest(row.investment) }} <i class="unit">万</i></span>
          <span v-else class="dim">—</span>
        </template>
      </el-table-column>
      <el-table-column label="材料" width="86">
        <template #default="{ row }">
          <span :class="{ dim: !row.ready_count }">{{ row.ready_count }}/{{ row.file_count }} 就绪</span>
        </template>
      </el-table-column>
      <el-table-column label="字数" width="76" align="right">
        <template #default="{ row }">
          <span class="dim">{{ (row.char_total / 1000).toFixed(1) }}k</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="76">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'in_use' ? 'success' : 'info'" effect="plain">
            {{ row.status_label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" align="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">查看</el-button>
          <el-button v-if="canEdit" link type="danger" @click="removeProject(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="table-foot">
      <span class="foot-count">共 {{ total }} 个项目</span>
      <el-pagination
        v-model:current-page="filters.page"
        :page-size="filters.size"
        :total="total"
        layout="prev, pager, next"
        @current-change="loadList"
      />
    </div>
    </div>

    <!-- 年度核对 -->
    <el-dialog v-model="yearShow" title="年度核对" width="780px">
      <div class="year-tip">
        下列档案的「库中年度」与新识别结果不一致。新规则优先取封面申报日期，其次文件名，
        最后才取正文年份。请逐条确认，确属不同的保留原值。
      </div>
      <el-table
        v-loading="yearLoading"
        :data="yearItems"
        size="small"
        max-height="420"
        empty-text="没有需要修正的项目"
        @selection-change="(v) => (yearPicked = v.map((x) => x.id))"
      >
        <el-table-column type="selection" width="42" />
        <el-table-column prop="name" label="项目" min-width="230" show-overflow-tooltip />
        <el-table-column label="库中" width="78">
          <template #default="{ row }">{{ row.old_year || '—' }}</template>
        </el-table-column>
        <el-table-column label="识别" width="78">
          <template #default="{ row }">{{ row.new_year }}</template>
        </el-table-column>
        <el-table-column prop="file_year_source" label="识别依据" width="130" />
      </el-table>
      <template #footer>
        <span class="year-count">共 {{ yearItems.length }} 项，已选 {{ yearPicked.length }}</span>
        <el-button @click="yearShow = false">取消</el-button>
        <el-button type="primary" :loading="yearApplying" @click="applyYears">应用选中</el-button>
      </template>
    </el-dialog>

    <!-- 导入 -->
    <el-drawer v-model="importShow" title="导入历史材料" size="620px">
      <div class="drawer-body">
        <el-upload
          drag
          multiple
          :auto-upload="false"
          :show-file-list="false"
          accept=".pdf,.docx,.txt,.md"
          :on-change="onPick"
        >
          <div class="up-title">把历史可研报告拖到这里，或点击选择</div>
          <div class="up-sub">
            PDF / DOCX / TXT，可多选，单份 200MB 内<br />
            扫描件需先做 OCR；.doc 请另存为 .docx
          </div>
        </el-upload>

        <div v-if="pending.length" class="pending">
          <div class="pending-head">
            待导入 {{ pending.length }} 份，合计 {{ pendingSize.toFixed(1) }}MB
          </div>
          <div v-for="(f, i) in pending" :key="f.name + f.size" class="pending-item">
            <span class="pi-name">{{ f.name }}</span>
            <span class="pi-size">{{ (f.size / 1024).toFixed(0) }} KB</span>
            <el-button link type="danger" size="small" @click="removePending(i)">移除</el-button>
          </div>
        </div>

        <div class="form-row">
          <div class="field">
            <label>归入已有项目（可选）</label>
            <el-select v-model="importForm.project" placeholder="留空则按报告名称自动归组" clearable style="width: 100%">
              <el-option v-for="r in rows" :key="r.id" :label="r.name" :value="r.id" />
            </el-select>
          </div>
          <div class="field" style="width: 150px">
            <label>默认年度</label>
            <el-input-number v-model="importForm.year" :min="1990" :max="2100" controls-position="right" style="width: 100%" />
          </div>
        </div>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="重复文件自动跳过；名称、单位、投资从正文抽取，导入后可改。"
        />

        <div v-if="importing" class="progress">
          <el-progress :percentage="uploadPct" :stroke-width="6" />
          <div class="progress-tip">
            正在解析，PDF 较慢，请勿关闭页面。
          </div>
        </div>

        <div v-if="results" class="results">
          <div class="results-head">
            导入结果：成功 {{ results.summary.ok }} · 跳过 {{ results.summary.skipped }} ·
            失败 {{ results.summary.failed }}
          </div>
          <div v-for="(r, i) in results.results" :key="i" class="result-item" :class="r.ok ? (r.skipped ? 'skip' : 'ok') : 'fail'">
            <span class="dot-i">{{ r.ok ? (r.skipped ? '—' : '✓') : '✕' }}</span>
            <div>
              <div class="ri-name">{{ r.filename }}</div>
              <div class="ri-msg">{{ r.message }}</div>
            </div>
          </div>
        </div>
      </div>

      <template #footer>
        <el-button @click="importShow = false">关闭</el-button>
        <el-button type="primary" :loading="importing" @click="doImport">开始导入</el-button>
      </template>
    </el-drawer>

    <!-- 详情 -->
    <el-drawer v-model="detailShow" :title="detail?.name || '历史项目'" size="720px">
      <div v-if="detail" class="drawer-body">
        <div class="detail-actions">
          <el-button v-if="canEdit && !editing" size="small" @click="editing = true">编辑信息</el-button>
          <template v-if="editing">
            <el-button size="small" type="primary" @click="saveEdit">保存</el-button>
            <el-button size="small" @click="editing = false">取消</el-button>
          </template>
        </div>

        <el-form v-if="editing" label-width="86px" label-position="left" class="meta-form">
          <el-form-item label="项目名称"><el-input v-model="editForm.name" /></el-form-item>
          <el-form-item label="建设单位"><el-input v-model="editForm.unit" /></el-form-item>
          <el-form-item label="立项年度">
            <el-input-number v-model="editForm.year" :min="1990" :max="2100" controls-position="right" />
          </el-form-item>
          <el-form-item label="总投资(万)">
            <el-input-number v-model="editForm.investment" :min="0" :precision="2" controls-position="right" />
          </el-form-item>
          <el-form-item label="业务领域"><el-input v-model="editForm.category" placeholder="如 不动产登记、医保、教育" /></el-form-item>
          <el-form-item label="运行状态">
            <el-select v-model="editForm.status">
              <el-option v-for="s in STATUS_OPTIONS" :key="s.value" :label="s.label" :value="s.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="备注"><el-input v-model="editForm.note" type="textarea" :rows="2" /></el-form-item>
        </el-form>

        <div v-else class="meta">
          <div class="meta-item"><label>建设单位</label><span>{{ detail.unit || '—' }}</span></div>
          <div class="meta-item"><label>立项年度</label><span>{{ detail.year || '—' }}</span></div>
          <div class="meta-item"><label>总投资</label><span>{{ detail.investment ? fmtInvest(detail.investment) + ' 万元' : '—' }}</span></div>
          <div class="meta-item"><label>业务领域</label><span>{{ detail.category || '—' }}</span></div>
          <div class="meta-item"><label>运行状态</label><span>{{ detail.status_label }}</span></div>
          <div class="meta-item"><label>入库时间</label><span>{{ detail.created }}</span></div>
          <div v-if="detail.note" class="meta-note">{{ detail.note }}</div>
        </div>

        <div class="sec-title">材料（{{ detail.files.length }} 份）</div>
        <div v-for="f in detail.files" :key="f.id" class="file">
          <div class="file-head" @click="toggleFile(f)">
            <div class="file-main">
              <div class="file-name">{{ f.filename }}</div>
              <div class="file-sub">
                {{ f.doc_type_label }} · {{ (f.size_mb || 0).toFixed(1) }}MB ·
                {{ f.page_count ? f.page_count + ' 页 · ' : '' }}{{ f.char_count }} 字 ·
                {{ f.created }}
              </div>
            </div>
            <el-button
              v-if="canReadRaw"
              link
              type="primary"
              size="small"
              class="open-raw"
              @click.stop="openRaw(f)"
            >原件</el-button>
            <el-tag size="small" :type="f.status === 'ready' ? 'success' : f.status === 'failed' ? 'danger' : 'info'" effect="plain">
              {{ f.status_label }}
            </el-tag>
          </div>

          <div v-if="f.status === 'failed'" class="file-error">{{ f.error }}</div>

          <div v-if="openedFile === f.id && fileDetail" class="file-body">
            <div class="file-tools">
              <el-button v-if="canReadRaw" link type="primary" size="small" @click="openRaw(f)">打开原件</el-button>
              <el-button link type="primary" size="small" @click="showText(f)">查看全文</el-button>
              <el-button v-if="canEdit" link size="small" @click="reparse(f)">重新解析</el-button>
              <el-button v-if="canEdit" link type="danger" size="small" @click="removeFile(f)">删除</el-button>
            </div>
            <div class="sec-note">
              识别出 {{ fileDetail.sections.length }} 个章节 ·
              {{ fileFns.biz.length }} 项业务功能
              <span v-if="fileFns.gen.length">· {{ fileFns.gen.length }} 项通用能力</span>
            </div>
            <div class="func-tags">
              <el-tag v-for="fn in fileFns.biz" :key="fn.name" size="small" effect="plain">
                {{ fn.name }}
              </el-tag>
              <el-tag v-for="fn in fileFns.gen" :key="'g' + fn.name" size="small" type="info" effect="plain">
                {{ fn.name }}
              </el-tag>
            </div>
            <div class="sec-note" style="margin-top: 10px">章节（用于按章对齐比对）</div>
            <table class="sec-table">
              <tr v-for="(s, i) in fileDetail.sections" :key="i">
                <td class="sec-t">{{ s.title }}</td>
                <td class="sec-c">{{ s.category_label }}</td>
                <td class="sec-n">{{ s.chars }} 字</td>
              </tr>
            </table>
          </div>
        </div>
      </div>
    </el-drawer>

    <!-- 全文 -->
    <el-dialog v-model="textShow" :title="fileText.filename" width="820px" top="6vh">
      <pre class="full-text">{{ fileText.text }}</pre>
    </el-dialog>

    <!-- 原件预览 -->
    <RawPreview v-model="rawVisible" :url="rawUrl" :filename="rawName" />

    <!-- 比对 -->
    <el-drawer v-model="compareShow" title="重复建设比对" size="560px">
      <div class="drawer-body">
        <el-radio-group v-model="compareMode" class="mode">
          <el-radio-button value="file">上传新申报材料</el-radio-button>
          <el-radio-button value="text">粘贴正文</el-radio-button>
          <el-radio-button value="library">选已有档案互查</el-radio-button>
        </el-radio-group>

        <template v-if="compareMode === 'file'">
          <el-upload drag :auto-upload="false" :show-file-list="false" accept=".pdf,.docx,.txt,.md" :on-change="onComparePick">
            <div class="up-title">选择要申报的可研报告</div>
            <div class="up-sub">材料不会被存入档案库，只做一次性比对</div>
          </el-upload>
          <div v-if="compareFile" class="picked">已选：{{ compareFile.name }}</div>
          <div class="field">
            <label>项目名称（可选，填了更准）</label>
            <el-input v-model="compareText.name" placeholder="留空则从报告中自动抽取" />
          </div>
        </template>

        <template v-else-if="compareMode === 'text'">
          <div class="field">
            <label>项目名称</label>
            <el-input v-model="compareText.name" placeholder="可选" />
          </div>
          <div class="field">
            <label>正文</label>
            <el-input v-model="compareText.text" type="textarea" :rows="12" placeholder="粘贴建设内容、需求分析等章节正文，至少 100 字" />
          </div>
        </template>

        <template v-else>
          <div class="field">
            <label>选择项目</label>
            <el-select v-model="libraryPick.project" placeholder="从历史档案里选" style="width: 100%" @change="libraryPick.file = null">
              <el-option v-for="r in rows" :key="r.id" :label="r.name" :value="r.id" />
            </el-select>
          </div>
          <div class="field">
            <label>选择材料</label>
            <el-select v-model="libraryPick.file" placeholder="选一份已就绪的材料" style="width: 100%">
              <el-option v-for="f in fileOptions" :key="f.id" :label="f.filename" :value="f.id" />
            </el-select>
          </div>
          <div class="hint-line">比对时会自动排除这份材料自身。</div>
        </template>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="只列疑点，是否构成重复建设由专家核定。"
        />
      </div>
      <template #footer>
        <el-button @click="compareShow = false">取消</el-button>
        <el-button type="primary" :loading="comparing" @click="runCompare">开始比对</el-button>
      </template>
    </el-drawer>

    <!-- 报告 -->
    <el-drawer v-model="reportShow" title="重复建设疑点筛查（结论待专家核定）" size="78%">
      <CompareReport v-if="report" :report="report" />
    </el-drawer>

    <!-- 核定台账：谁在什么时候、基于哪几条疑点、认定了什么 -->
    <el-drawer v-model="reviewShow" title="重复建设核定台账" size="620px">
      <div class="rv-toolbar">
        <el-radio-group v-model="reviewFilter" size="small" @change="loadReviews">
          <el-radio-button label="">全部</el-radio-button>
          <el-radio-button label="duplicate">构成重复</el-radio-button>
          <el-radio-button label="not_duplicate">不构成重复</el-radio-button>
          <el-radio-button label="need_more">需补材料</el-radio-button>
        </el-radio-group>
      </div>
      <div v-loading="reviewLoading">
        <div v-if="!reviewRows.length" class="hint-line">
          还没有核定记录。在比对报告的每一项下由专家核定后，记录会出现在这里。
        </div>
        <div v-for="r in reviewRows" :key="r.id" class="rv-row">
          <div class="rv-row-head">
            <span class="rv-verdict" :class="'vf-' + r.verdict">{{ r.verdict_label }}</span>
            <span class="rv-row-time">{{ fmtTime(r.created) }} · {{ r.reviewer_display || '未署名' }}</span>
          </div>
          <div class="rv-row-target">
            {{ r.query_name || '（未命名申报材料）' }}
            <span class="rv-arrow">→</span>
            {{ r.project_name }}
          </div>
          <p class="rv-row-basis">{{ r.basis || '（未填写依据）' }}</p>
          <div class="rv-row-foot">基于 {{ (r.findings || []).length }} 条疑点核定</div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.arch { max-width: 1480px; }

.top-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.top-row .stat-strip { flex: 1 1 620px; margin-bottom: 0; }
.top-actions { display: flex; align-items: center; gap: 12px; flex: 0 0 auto; padding-bottom: 2px; }

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
}
.search { width: 260px; }
.search-icon { color: var(--ink-4); }
.pick { width: 132px; }
.hint { font-size: 11.5px; color: var(--ink-4); margin-left: auto; }

.table-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}
.table { background: transparent; }
.table :deep(th.el-table__cell) { background: var(--panel-2); }
.table-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-top: 1px solid var(--line-soft);
  background: var(--panel-2);
}
.foot-count { font-size: 12px; color: var(--ink-3); }

.proj-name { font-size: 13.5px; cursor: pointer; color: var(--ink); line-height: 1.45; }
.proj-name:hover { color: var(--brand); }
.proj-sub { font-size: 11.5px; color: var(--ink-4); margin-top: 2px; }
.unit { font-style: normal; font-size: 12px; color: var(--ink-3); }
.dim { color: var(--ink-4); }
.year-src {
  font-size: 9.5px; color: var(--brand); background: var(--brand-bg);
  border-radius: 3px; padding: 0 3px; margin-left: 4px; vertical-align: 1px;
}

.drawer-body { padding-right: 4px; }

.up-title { font-size: 13.5px; color: var(--ink-2); }
.up-sub { font-size: 11.5px; color: var(--ink-3); line-height: 1.8; margin-top: 6px; }

.pending { margin: 14px 0; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.pending-head { padding: 7px 12px; background: var(--panel-2); font-size: 12px; color: var(--ink-2); }
.pending-item { display: flex; align-items: center; gap: 10px; padding: 5px 12px; border-top: 1px solid var(--line-soft); }
.pi-name { flex: 1; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pi-size { font-size: 12px; color: var(--ink-4); }

.form-row { display: flex; gap: 12px; margin: 14px 0; }
.field { flex: 1; margin-bottom: 10px; }
.field label { display: block; font-size: 11.5px; color: var(--ink-3); margin-bottom: 4px; }

.progress { margin: 14px 0; }
.progress-tip { font-size: 11.5px; color: var(--ink-3); margin-top: 6px; line-height: 1.7; }

.results { margin-top: 14px; }
.results-head { font-size: 12.5px; margin-bottom: 8px; }
.result-item { display: flex; gap: 8px; padding: 7px 10px; border-radius: 7px; margin-bottom: 6px; font-size: 12px; }
.result-item.ok { background: var(--ok-bg); }
.result-item.skip { background: var(--panel-2); }
.result-item.fail { background: var(--danger-bg); }
.dot-i { width: 12px; }
.ri-name { color: var(--ink); }
.ri-msg { color: var(--ink-3); font-size: 11.5px; margin-top: 1px; }

.detail-actions { display: flex; gap: 12px; justify-content: flex-end; margin-bottom: 16px; }
.meta { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 24px; margin-bottom: 14px; }
.meta-item { display: flex; gap: 8px; font-size: 12.5px; }
.meta-item label { color: var(--ink-3); flex: 0 0 62px; }
.meta-note { grid-column: 1 / -1; font-size: 12.5px; color: var(--ink-2); background: var(--panel-2); padding: 8px 10px; border-radius: 6px; }

.meta-form { margin-bottom: 10px; }

.sec-title { font-size: 12.5px; color: var(--ink-2); margin: 16px 0 8px; }
.file { border: 1px solid var(--line); border-radius: 8px; margin-bottom: 8px; overflow: hidden; }
.file-head { display: flex; align-items: center; gap: 12px; padding: 10px 12px; cursor: pointer; }
.file-head:hover { background: var(--panel-2); }
.file-main { flex: 1; min-width: 0; }
.file-name { font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-sub { font-size: 12px; color: var(--ink-3); margin-top: 2px; }
.open-raw { flex: none; }
.file-error { padding: 0 12px 10px; font-size: 11.5px; color: var(--danger); }
.file-body { padding: 8px 12px 12px; border-top: 1px solid var(--line-soft); }
.file-tools { display: flex; gap: 4px; margin-bottom: 6px; }
.sec-note { font-size: 11.5px; color: var(--ink-3); margin-bottom: 6px; }
.func-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.sec-table { width: 100%; font-size: 12px; border-collapse: collapse; }
.sec-table tr { border-bottom: 1px dashed var(--line-soft); }
.sec-table td { padding: 4px 0; }
.sec-t { color: var(--ink-2); }
.sec-c { width: 96px; color: var(--brand); font-size: 11.5px; }
.sec-n { width: 66px; text-align: right; color: var(--ink-4); font-size: 12px; }

/* 全文在抽屉里整幅铺开，滚动交给抽屉本身，不再套一层内滚 */
.full-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-sans);
  font-size: 13px;
  line-height: 1.95;
  color: var(--ink-2);
  padding: 16px 18px;
  background: var(--panel-2);
  border: 1px solid var(--line-soft);
  border-radius: 10px;
}

.mode { margin-bottom: 16px; }
.picked { font-size: 12.5px; color: var(--ok); margin: 8px 0; }
.hint-line { font-size: 11.5px; color: var(--ink-3); }

.year-tip {
  font-size: 12px;
  line-height: 1.9;
  color: var(--ink-2);
  background: var(--panel-2);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 12px;
}

.year-count { float: left; font-size: 12px; color: var(--ink-3); line-height: 32px; }

/* 核定台账 */
.rv-toolbar { margin-bottom: 12px; }
.rv-row {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--panel);
}
.rv-row-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rv-verdict {
  font-size: 11.5px;
  padding: 2px 8px;
  border-radius: 5px;
  font-weight: 500;
}
.vf-duplicate { color: var(--danger); background: var(--danger-bg); }
.vf-not_duplicate { color: var(--success); background: var(--success-soft); }
.vf-need_more { color: var(--warn); background: var(--warn-bg); }
.rv-row-time { font-size: 12px; color: var(--ink-4); }
.rv-row-target { font-size: 12.5px; color: var(--ink); margin-top: 6px; }
.rv-arrow { color: var(--ink-4); margin: 0 4px; }
.rv-row-basis {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.8;
  padding: 7px 9px;
  border-radius: 6px;
  background: var(--panel-2);
}
.rv-row-foot { font-size: 12px; color: var(--ink-4); margin-top: 6px; }
</style>
