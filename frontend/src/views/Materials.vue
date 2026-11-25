<script setup>
import { STAGE_STYLE } from '@/constants/stages'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { projectApi } from '@/api'
import { showError } from '@/api/http'
import { useUserStore } from '@/stores/user'
import { useRawFile } from '@/composables/useRawFile'
import RawPreview from '@/components/RawPreview.vue'
import MaterialUploadDialog from '@/components/MaterialUploadDialog.vue'
import { downloadBlob } from '@/utils/fileview'

/* 【已下线】跨项目的「项目资料」汇总页 —— 2026-11-24 20:05 摘掉导航与路由。
   原因：材料属于具体的件，人在某个项目范围内看资料，不该先跳到一张跨项目列表上
   再找回自己那个件。材料清单现在只在项目详情页的「项目资料」页签里看、在详情页上传。
   本文件保留作参考（没被任何路由/组件引用，不参与打包），确认不再需要后可删。 */

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const ORDER = [
  { key: 'draft', label: '申报材料' },
  { key: 'precheck', label: '预审中' },
  { key: 'supplement', label: '待补正' },
  { key: 'expert_review', label: '评审中' },
  { key: 'review_supplement', label: '评审后补正' },
  { key: 'cost_review', label: '费用评审' },
  { key: 'cost_supplement', label: '征求反馈' },
  { key: 'fiscal_review', label: '财政审核' },
  { key: 'approval', label: '立项' },
  { key: 'tendering', label: '待招标' },
  { key: 'building', label: '建设中' },
  { key: 'trial', label: '试运行' },
  { key: 'acceptance', label: '待验收' },
  { key: 'accepted', label: '已验收' },
]
const ORDER_KEYS = ORDER.map((s) => s.key)
const stageFolderName = (key) => ORDER.find((s) => s.key === key)?.label
  || { rejected: '不予通过', reserve: '项目储备库' }[key] || '其他环节'



/* ------------------------------------------------------------ 项目列表 */
const q = ref('')
const listLoading = ref(false)
const projects = ref([])
const pid = ref(null)
const p = ref(null)
const detailLoading = ref(false)

// #33：左侧项目列表原来固定取前 100 条且没有翻页，项目一多就只能靠搜索碰运气。
// 现在按真实分页拉取，带总数与每页条数切换。
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

function resetPageAndLoad() {
  page.value = 1
  loadProjects(false)
}

function changePage(v) {
  page.value = v || 1
  loadProjects()
}

function changeSize(v) {
  pageSize.value = v
  page.value = 1
  loadProjects(false)
}

async function loadProjects(keepPick = true) {
  listLoading.value = true
  try {
    const { data } = await projectApi.list({
      q: q.value || undefined, page: page.value, page_size: pageSize.value,
    })
    projects.value = data.results || []
    total.value = Number(data.count) || 0
    const exists = projects.value.some((r) => r.id === pid.value)
    if (!keepPick || !exists) {
      // 进页面时优先用地址里带的项目（从详情页「打开项目资料」过来），
      // 其次选第一个 —— 不能因为列表刷新把当前看的项目踢掉
      const want = Number(route.query.project)
      const hit = projects.value.find((r) => r.id === want)
      pid.value = hit ? hit.id : (projects.value[0]?.id ?? null)
    }
    if (pid.value && pid.value !== p.value?.id) await loadDetail(pid.value)
  } finally {
    listLoading.value = false
  }
}

async function loadDetail(id) {
  if (!id) { p.value = null; return }
  detailLoading.value = true
  try {
    const { data } = await projectApi.detail(id)
    p.value = data
  } finally {
    detailLoading.value = false
  }
}

function pick(row) {
  if (row.id === pid.value) return
  pid.value = row.id
  openedFolders.value = null
  showHistory.value = false
  router.replace({ name: 'materials', query: { project: row.id } })
  loadDetail(row.id)
}

/* ------------------------------------------------------------ 权限 */
// 与项目详情页同一口径：申报单位在申报与各补正环节能换材料，
// 管理员只在申报阶段补；其余角色看得到、改不了
const canUpload = computed(() => {
  if (!p.value) return false
  if (userStore.role === 'admin') return p.value.stage === 'draft'
  return userStore.role === 'applicant'
    && p.value.applicant === userStore.user?.id
    && ['draft', 'supplement', 'review_supplement', 'cost_supplement'].includes(p.value.stage)
})

/* ------------------------------------------------------------ 文件夹 */
const showHistory = ref(false)
const visibleFiles = computed(() => {
  const all = p.value?.files || []
  if (showHistory.value) return all
  return all.filter((f) => f.is_current !== false)
})
const historyCount = computed(() => (p.value?.files || []).filter((f) => f.is_current === false).length)
const allFileCount = computed(() => (p.value?.files || []).length)

const folders = computed(() => {
  const groups = new Map()
  const push = (key, label) => {
    if (!groups.has(key)) groups.set(key, { key, label, files: [], docs: [], count: 0, latest: '' })
    return groups.get(key)
  }
  for (const f of visibleFiles.value) {
    const g = push(f.stage || 'draft', f.stage_label || stageFolderName(f.stage))
    g.files.push(f)
  }
  for (const d of (p.value?.documents || [])) {
    const g = push(d.stage || 'draft', d.stage_label || stageFolderName(d.stage))
    g.docs.push(d)
  }
  const list = [...groups.values()]
  for (const g of list) {
    g.docs.sort((a, b) => (b.created || '').localeCompare(a.created || ''))
    g.count = g.files.length + g.docs.length
    const times = [...g.files.map((f) => f.created), ...g.docs.map((d) => d.created)].filter(Boolean)
    g.latest = times.sort().pop() || ''
  }
  return list.sort((a, b) => {
    const ia = ORDER_KEYS.indexOf(a.key)
    const ib = ORDER_KEYS.indexOf(b.key)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
  })
})

// 默认展开当前环节与申报材料两摞；当前环节没有材料就退一步展开最近更新的一摞，
// 免得进来看到一列全收起的文件夹，以为材料没归上
const defaultOpen = computed(() => {
  const keys = new Set()
  const has = (k) => folders.value.some((g) => g.key === k)
  if (has(p.value?.stage)) keys.add(p.value.stage)
  if (has('draft')) keys.add('draft')
  if (!keys.size && folders.value.length) {
    const newest = [...folders.value].sort((a, b) => (b.latest || '').localeCompare(a.latest || ''))[0]
    keys.add(newest.key)
  }
  return keys
})
const openedFolders = ref(null)
const folderOpen = (key) => (openedFolders.value || defaultOpen.value).has(key)
function toggleFolder(key) {
  const cur = new Set(openedFolders.value || defaultOpen.value)
  if (cur.has(key)) cur.delete(key)
  else cur.add(key)
  openedFolders.value = cur
}

/* ------------------------------------------------------------ 上传 */
// 上传对话框是共用组件（与项目详情页同一份），这里只管开与收结果
const uploadShow = ref(false)

function onUploaded(data) {
  if (data?.project) p.value = data.project
  loadProjects(false)
}

/* ------------------------------------------------------------ 文件操作 */
const {
  visible: rawVisible, url: rawUrl, name: rawName, open: openRawFile,
} = useRawFile()

function openRaw(row) {
  openRawFile(() => projectApi.fileRaw(pid.value, row.id), row.filename,
    () => projectApi.fileOpen(pid.value, row.id))
}

const textShow = ref(false)
const textData = ref(null)
const textLoading = ref(false)

async function viewText(row) {
  textShow.value = true
  textLoading.value = true
  textData.value = null
  try {
    const { data } = await projectApi.fileText(pid.value, row.id)
    textData.value = data
  } finally {
    textLoading.value = false
  }
}

const diffShow = ref(false)
const diffLoading = ref(false)
const diffData = ref(null)
const diffTarget = ref(null)

async function openDiff(row) {
  diffTarget.value = row
  diffData.value = null
  diffShow.value = true
  diffLoading.value = true
  try {
    const { data } = await projectApi.fileDiff(pid.value, row.id)
    diffData.value = data
  } catch {
    diffShow.value = false
  } finally {
    diffLoading.value = false
  }
}

const DIFF_LABEL = { add: '新增', del: '删除', change: '改写' }
const diffLabel = (t) => DIFF_LABEL[t] || t
const fmtChars = (n) => (n || 0).toLocaleString('zh-CN')

async function removeFile(row) {
  try {
    await ElMessageBox.confirm(`删除《${row.filename}》？解析结果一并删除。`, '提示', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  try {
    await projectApi.removeFile(pid.value, row.id)
    ElMessage.success('已删除')
    await loadDetail(pid.value)
    loadProjects(false)
  } catch (e) {
    showError(e?.response?.data?.detail || '删除失败')
  }
}

async function downloadDoc(doc) {
  if (!doc) return
  try {
    const { data } = await projectApi.documentFile(pid.value, doc.id)
    downloadBlob(data, doc.name)
  } catch {
    ElMessage.error('文书下载失败，请稍后重试')
  }
}

function goDetail() {
  if (!pid.value) return
  const q = {}
  if (route.query.view) q.view = route.query.view
  if (route.query.from) q.from = route.query.from
  router.push({ name: 'project-detail', params: { id: pid.value }, query: q })
}

const fmtTime = (v) => (v ? String(v).replace('T', ' ').slice(0, 16) : '—')

onMounted(async () => {
  await loadProjects()
  // 老链接兼容：带 ?upload=1 进来仍直接开上传框
  // （详情页已改成就地弹框，不再产生这个参数）
  if (route.query.upload === '1' && canUpload.value) {
    uploadShow.value = true
    const q = { project: pid.value }
    router.replace({ name: 'materials', query: q })
  }
})

// 从别处带 ?project=xx 再进来（同一个路由只改 query）时切过去
watch(() => route.query.project, (v) => {
  const id = Number(v)
  if (!id || id === pid.value) return
  if (!projects.value.some((r) => r.id === id)) return
  pid.value = id
  openedFolders.value = null
  loadDetail(id)
})
</script>

<template>
  <div class="mat">
    <!-- 左栏：项目列表。资料是按项目归的，先选件再翻文件夹 -->
    <aside class="mat-side card">
      <div class="ms-head">
        <el-input
          v-model="q"
          placeholder="搜项目名称、编号、单位"
          clearable
          size="small"
          @keyup.enter="resetPageAndLoad"
          @clear="resetPageAndLoad"
        />
      </div>
      <div v-loading="listLoading" class="ms-list">
        <button
          v-for="row in projects"
          :key="row.id"
          type="button"
          class="ms-item"
          :class="{ on: row.id === pid }"
          @click="pick(row)"
        >
          <div class="ms-name">{{ row.name }}</div>
          <div class="ms-meta">
            <span class="mono ms-code">{{ row.code }}</span>
            <span class="stage-tag" :class="'st-' + (STAGE_STYLE[row.stage] || 'wait')">
              {{ row.stage_label }}
            </span>
            <span class="ms-count" :class="{ zero: !row.file_count }">{{ row.file_count }} 份</span>
          </div>
        </button>
        <div v-if="!listLoading && !projects.length" class="ms-empty muted">
          没有匹配的项目。
        </div>
      </div>
      <div v-if="total" class="ms-pager">
        <span class="ms-total">共 {{ total }} 个项目</span>
        <el-select
          :model-value="pageSize"
          size="small"
          class="ms-size"
          @change="changeSize"
        >
          <el-option v-for="n in [20, 50, 100, 200]" :key="n" :label="`${n}/页`" :value="n" />
        </el-select>
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          small
          background
          @current-change="changePage"
        />
      </div>
    </aside>

    <!-- 右栏：该项目的资料，按环节分文件夹 -->
    <section class="mat-main card">
      <div v-if="!p" class="mm-empty muted">
        {{ listLoading ? '正在读取项目…' : '左侧选一个项目' }}
      </div>

      <template v-else>
        <header class="mm-head">
          <div class="mm-title-row">
            <span class="mm-name">{{ p.name }}</span>
            <span class="mm-code mono">{{ p.code }}</span>
            <span class="stage-tag" :class="'st-' + (STAGE_STYLE[p.stage] || 'wait')">
              {{ p.stage_label }}
            </span>
          </div>
          <div class="mm-foot">
            <span class="mm-sub">{{ p.unit }} · 共 {{ allFileCount }} 份</span>
            <div class="mm-ops">
              <el-button
                v-if="historyCount"
                link type="primary" size="small"
                @click="showHistory = !showHistory"
              >{{ showHistory ? '只看当前版本' : `含历史版本（${historyCount}）` }}</el-button>
              <el-button size="small" @click="goDetail">项目详情</el-button>
              <el-button v-if="canUpload" type="primary" size="small" @click="uploadShow = true">
                上传材料
              </el-button>
            </div>
          </div>
        </header>

        <div v-loading="detailLoading" class="mm-body">
          <div v-if="!folders.length" class="doc-empty muted">
            这个项目还没有材料。{{ canUpload ? '点右上「上传材料」开始，可研、概算、批复各归各的环节文件夹。' : '' }}
          </div>

          <div v-else class="pf">
            <div v-for="g in folders" :key="g.key" class="pf-group" :class="{ open: folderOpen(g.key) }">
              <button type="button" class="pf-folder" @click="toggleFolder(g.key)">
                <span class="pf-caret">{{ folderOpen(g.key) ? '▾' : '▸' }}</span>
                <span class="pf-folder-name">{{ g.label }}</span>
                <span class="pf-count">{{ g.count }} 份</span>
                <span v-if="g.key === p.stage" class="pf-now">当前环节</span>
                <span class="pf-latest">{{ g.latest ? fmtTime(g.latest) : '' }}</span>
              </button>

              <div v-show="folderOpen(g.key)" class="pf-body">
                <div v-for="f in g.files" :key="'f' + f.id" class="pf-item">
                  <div class="pf-title">
                    <span class="pf-file">{{ f.filename }}</span>
                    <span v-if="f.version > 1" class="ver-tag">
                      第 {{ f.round }} 轮 · 第 {{ f.version }} 版
                    </span>
                    <span v-if="!f.is_current" class="ver-old">已被新版取代</span>
                  </div>
                  <div class="pf-meta">
                    <span>{{ f.doc_type_label }}</span>
                    <i>·</i><span>{{ f.size_mb }} MB</span>
                    <i>·</i><span :class="['st-dot', f.status]">{{ f.status_label }}</span>
                    <template v-if="f.uploaded_by_name">
                      <i>·</i><span>{{ f.uploaded_by_name }}</span>
                    </template>
                    <i>·</i><span>{{ fmtTime(f.created) }}</span>
                    <span class="pf-ops">
                      <el-button link type="primary" @click="openRaw(f)">原件</el-button>
                      <el-button v-if="f.status === 'ready'" link type="primary" @click="viewText(f)">
                        看正文
                      </el-button>
                      <el-button v-if="f.version > 1" link type="primary" @click="openDiff(f)">
                        与上版对比
                      </el-button>
                      <el-button v-if="canUpload" link type="danger" @click="removeFile(f)">
                        删除
                      </el-button>
                    </span>
                  </div>
                  <div v-if="f.error" class="ferr">{{ f.error }}</div>
                </div>

                <div v-for="d in g.docs" :key="'d' + d.id" class="pf-item">
                  <div class="pf-title">
                    <span class="pf-file">{{ d.name }}</span>
                    <span class="doc-kind">{{ d.kind_label }}</span>
                  </div>
                  <div class="pf-meta">
                    <span>系统生成</span>
                    <i>·</i><span>{{ d.created_by_name }}</span>
                    <i>·</i><span>{{ fmtTime(d.created) }}</span>
                    <span class="pf-ops">
                      <el-button link type="primary" @click="downloadDoc(d)">下载</el-button>
                    </span>
                  </div>
                </div>

                <div v-if="g.count === 0" class="pf-empty muted">这一环节还没有材料。</div>
              </div>
            </div>
          </div>

          <div class="foot-tip">
            PDF / DOCX / TXT / XLSX，单份 200MB 内
          </div>
        </div>
      </template>
    </section>

    <!-- 上传：与项目详情页共用同一个对话框组件 -->
    <MaterialUploadDialog
      v-model="uploadShow"
      :project-id="pid"
      @uploaded="onUploaded"
    />

    <!-- 补正前后对照 -->
    <el-drawer
      v-model="diffShow"
      :title="`补正前后对照 · ${diffTarget?.filename || ''}`"
      size="86%"
    >
      <div v-loading="diffLoading" class="diff-wrap">
        <template v-if="diffData">
          <div class="diff-head">
            <span class="diff-sum">{{ diffData.summary }}</span>
            <span class="diff-meta">
              第 {{ diffData.from.round }} 轮 · 第 {{ diffData.from.version }} 版
              → 第 {{ diffData.to.round }} 轮 · 第 {{ diffData.to.version }} 版
            </span>
          </div>
          <div class="diff-cols">
            <span>修改前 · {{ fmtChars(diffData.chars.before) }} 字</span>
            <span>修改后 · {{ fmtChars(diffData.chars.after) }} 字</span>
          </div>
          <div v-if="!diffData.blocks.length" class="dc-empty">
            正文没有段落级变化，可能只改了排版。
          </div>
          <div
            v-for="(b, i) in diffData.blocks"
            :key="i"
            class="diff-block"
            :class="'df-' + b.type"
          >
            <div class="diff-tag">{{ diffLabel(b.type) }}</div>
            <div class="diff-pair">
              <div class="diff-side"><pre>{{ b.before || '（这一版没有对应内容）' }}</pre></div>
              <div class="diff-side"><pre>{{ b.after || '（这一版已删除）' }}</pre></div>
            </div>
          </div>
          <div v-if="diffData.truncated" class="report-note">
            共 {{ diffData.total_blocks }} 处改动，只显示前 {{ diffData.blocks.length }} 处。
          </div>
        </template>
      </div>
    </el-drawer>

    <!-- 正文 -->
    <el-drawer v-model="textShow" :title="textData?.filename || '材料正文'" size="62%">
      <div v-loading="textLoading">
        <template v-if="textData">
          <div class="text-meta">
            共 {{ textData.char_count }} 字 · {{ (textData.sections || []).length }} 个章节
          </div>
          <div class="sec-list">
            <span v-for="(s, i) in textData.sections" :key="i" class="sec">
              {{ s.title }}<span class="sec-cat">{{ s.category_label }}</span>
            </span>
          </div>
          <pre class="raw-text">{{ textData.text }}</pre>
        </template>
      </div>
    </el-drawer>

    <!-- 原件预览 -->
    <RawPreview v-model="rawVisible" :url="rawUrl" :filename="rawName" />
  </div>
</template>

<style scoped>
.mat {
  display: grid;
  grid-template-columns: 292px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

/* ------------------------------------------------------------ 左：项目 */
.mat-side { overflow: hidden; }
.ms-head { padding: 10px 12px; border-bottom: 1px solid var(--line-soft); }
.ms-list {
  max-height: calc(100vh - 220px);
  overflow-y: auto;
  padding: 6px;
}
.ms-item {
  display: block; width: 100%; text-align: left; cursor: pointer;
  border: 1px solid transparent; border-radius: 8px;
  background: none; padding: 8px 9px; font-family: inherit;
  transition: background .15s, border-color .15s;
}
.ms-item:hover { background: var(--panel-3); }
.ms-item.on { background: var(--brand-bg); border-color: var(--primary-line); }
.ms-name {
  font-size: 12.5px; color: var(--ink); line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.ms-item.on .ms-name { color: var(--brand-deep); font-weight: 500; }
.ms-meta {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin-top: 5px; font-size: 12px; color: var(--ink-4);
}
.ms-code { font-size: 12px; }
.ms-count { margin-left: auto; font-variant-numeric: tabular-nums; }
.ms-count.zero { color: var(--ink-4); }
.ms-empty { padding: 18px 10px; text-align: center; }
.ms-pager {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  padding: 8px; border-top: 1px solid var(--line-soft);
}
.ms-total { font-size: 12px; color: var(--ink-4); }
.ms-size { width: 78px; }
.ms-pager :deep(.el-pagination) { flex: 1; justify-content: flex-end; }
.ms-pager :deep(.el-pager li) { min-width: 22px; }

/* ------------------------------------------------------------ 右：资料 */
.mat-main { padding: 14px 16px 12px; }
.mm-head { border-bottom: 1px solid var(--line-soft); padding-bottom: 10px; }
.mm-title-row { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.mm-name { font-size: 15px; font-weight: 600; color: var(--ink); letter-spacing: -0.1px; }
.mm-code { font-size: 12px; color: var(--ink-4); }
.mm-foot {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  margin-top: 12px;
}
.mm-sub { font-size: 12px; color: var(--ink-3); }
.mm-ops { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.mm-body { padding-top: 6px; }
.mm-empty { padding: 40px 0; text-align: center; }

/* ------------------------------------------------------------ 文件夹 */
.pf { display: flex; flex-direction: column; }
.pf-group + .pf-group { border-top: 1px solid var(--line-soft); }
.pf-folder {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 9px 2px; border: 0; background: none; cursor: pointer;
  font-family: inherit; font-size: 12.5px; color: var(--ink-2); text-align: left;
}
.pf-folder:hover .pf-folder-name { color: var(--brand); }
.pf-caret { width: 11px; font-size: 9px; color: var(--ink-4); }
.pf-folder-name { font-weight: 500; transition: color .15s; }
.pf-group.open .pf-folder-name { color: var(--brand-deep); }
.pf-count { font-size: 12px; color: var(--ink-4); }
.pf-now {
  font-size: 12px; line-height: 16px; padding: 0 6px; border-radius: 4px;
  background: var(--brand-bg); color: var(--brand-deep);
}
.pf-latest { margin-left: auto; font-size: 12px; color: var(--ink-4); }
.pf-body { padding: 1px 0 9px 19px; }
.pf-item { padding: 8px 0; border-top: 1px dashed var(--line-soft); }
.pf-item:first-child { border-top: 0; }
.pf-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pf-file { font-size: 12.5px; color: var(--ink); word-break: break-all; }
.pf-meta {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin-top: 3px; font-size: 12px; color: var(--ink-4);
}
.pf-meta i { font-style: normal; color: var(--line-strong); }
.pf-ops { margin-left: auto; display: inline-flex; align-items: center; gap: 2px; }
.pf-ops :deep(.el-button) { font-size: 11.5px; }
.pf-empty { padding: 7px 0 2px; font-size: 11.5px; }

.doc-empty { padding: 10px 0; font-size: 12.5px; line-height: 1.7; }
.doc-kind {
  font-size: 12px; line-height: 16px; padding: 0 6px; border-radius: 4px;
  background: var(--panel-3); color: var(--ink-3);
}
.ver-tag {
  font-size: 10px; line-height: 16px; padding: 0 5px; border-radius: 4px;
  background: var(--panel-3); color: var(--ink-3);
}
.ver-old { font-size: 10px; color: var(--ink-4); }
.ferr { font-size: 12px; color: var(--danger); margin-top: 2px; line-height: 1.6; }
.st-dot { font-size: 12px; }
.st-dot.ready { color: var(--ok); }
.st-dot.failed { color: var(--danger); }
.st-dot.pending, .st-dot.parsing { color: var(--ink-4); }
.foot-tip { font-size: 11.5px; color: var(--ink-4); padding-top: 8px; line-height: 1.7; }
.mono { font-family: var(--font-mono); }

/* ------------------------------------------------------------ 上传 */

/* ------------------------------------------------------------ 正文 / 对照 */
.text-meta { font-size: 11.5px; color: var(--ink-3); margin-bottom: 8px; }
.sec-list { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.sec { font-size: 12px; border: 1px solid var(--line-soft); border-radius: 5px; padding: 2px 7px; color: var(--ink-2); }
.sec-cat { color: var(--ink-4); margin-left: 4px; }
.raw-text {
  white-space: pre-wrap; word-break: break-word; font-family: var(--font-sans);
  font-size: 13px; line-height: 1.9; color: var(--ink-2);
  background: var(--panel-3); border-radius: 8px; padding: 14px 16px; margin: 0;
}
.report-note { font-size: 12px; color: var(--ink-4); line-height: 1.7; margin-top: 10px; }
.diff-wrap { min-height: 120px; }
.diff-head {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  padding-bottom: 10px; border-bottom: 1px solid var(--line-soft);
}
.diff-sum { font-size: 13px; color: var(--ink); }
.diff-meta { font-size: 11.5px; color: var(--ink-3); }
.diff-cols {
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
  font-size: 12px; color: var(--ink-4); padding: 8px 0;
}
.diff-block { border: 1px solid var(--line-soft); border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
.diff-tag { font-size: 12px; padding: 3px 8px; background: var(--panel-3); color: var(--ink-3); }
.df-add .diff-tag { background: var(--ok-bg); color: var(--ok); }
.df-del .diff-tag { background: var(--danger-bg); color: var(--danger); }
.df-change .diff-tag { background: var(--warn-bg); color: var(--warn); }
.diff-pair { display: grid; grid-template-columns: 1fr 1fr; }
.diff-side { padding: 8px 10px; border-right: 1px solid var(--line-soft); min-width: 0; }
.diff-side:last-child { border-right: none; }
.diff-side pre {
  margin: 0; white-space: pre-wrap; word-break: break-word;
  font-family: var(--font-sans); font-size: 12.5px; line-height: 1.85; color: var(--ink-2);
}
.df-del .diff-side:first-child pre { color: var(--danger); }
.df-add .diff-side:last-child pre { color: var(--ok); }
.dc-empty { padding: 20px 0; font-size: 12.5px; color: var(--ink-3); text-align: center; }

@media (max-width: 1080px) {
  .mat { grid-template-columns: minmax(0, 1fr); }
  .ms-list { max-height: 220px; }
}
</style>
