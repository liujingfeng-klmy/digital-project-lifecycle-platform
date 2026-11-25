<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { showError } from '@/api/http'
import { projectApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { fmtTime } from '@/utils/projectFormat'
import { downloadDoc } from '@/utils/projectFiles'
import { acceptOpinionsOf, collectDocOf } from '@/utils/projectDetailModel'

/* 验收材料两个阶段各收各的，别混：
 *  - 待验收：《数据归集证明》由我局按模板出具（正文取自单位报的试运行情况
 *    与归集情况），出具后才能验收 —— 建了不用、数据另起一套的项，只有卡在
 *    这一关才拦得住；
 *  - 已验收：验收会开完才有《专家验收意见》那张签字件，结项后补传备案。
 */
const props = defineProps({
  p: { type: Object, required: true },
  viewOnly: Boolean,
})
const emit = defineEmits(['refresh', 'open-raw'])

const userStore = useUserStore()
const collectDoc = computed(() => collectDocOf(props.p))
const acceptOpinions = computed(() => acceptOpinionsOf(props.p))

/** 出具证明与收存专家意见都是我局的审批动作，不是申报单位交材料 */
const canIssueProof = computed(() => !props.viewOnly
  && ['auditor', 'admin'].includes(userStore.role)
  && props.p?.stage === 'acceptance')
// 专家验收意见是结项后的备案件（验收会开完才有），与「本环节办理」无关：
// 从已办项目进详情会带 view=readonly，若跟着 viewOnly 一起藏掉，审核岗
// 事后就再也补不上了。这里只认角色 + 已验收阶段。
const canFileOpinion = computed(() => ['auditor', 'admin'].includes(userStore.role)
  && props.p?.stage === 'accepted')
const proofBusy = ref('')

/* 出具证明前把两段正文摊开：老件（升级前已到待验收）这两栏是空的，
   归集情况说不清的也要在这儿补齐 —— 证明不是空白章，总得有依据。
   改过的表述一并回写到项目上，保证证明与项目记录同一口径。 */
const issueDlg = ref(false)
const issueForm = reactive({ doc_no: '', trial_summary: '', data_collect_scope: '' })
const docBusy = ref(false)

function openIssue() {
  issueForm.doc_no = collectDoc.value?.meta?.doc_no || ''
  issueForm.trial_summary = props.p?.trial_summary || ''
  issueForm.data_collect_scope = props.p?.data_collect_scope || ''
  issueDlg.value = true
}

async function genDataCollect() {
  if ((issueForm.trial_summary || '').trim().length < 15) {
    ElMessage.warning('试运行情况至少 15 个字：证明里要写清运行时间与稳定情况')
    return
  }
  if ((issueForm.data_collect_scope || '').trim().length < 10) {
    ElMessage.warning('数据归集情况至少 10 个字：这份证明的核心就是它')
    return
  }
  docBusy.value = true
  try {
    const { data } = await projectApi.genDataCollect(props.p.id, {
      doc_no: issueForm.doc_no, trial_summary: issueForm.trial_summary,
      data_collect_scope: issueForm.data_collect_scope,
    })
    issueDlg.value = false
    ElMessage.success(data.detail)
    await downloadDoc(props.p.id, data.doc)
    emit('refresh')
  } catch (e) {
    showError(e?.response?.data?.detail || '生成失败')
  } finally {
    docBusy.value = false
  }
}

async function uploadProof(file, docType) {
  const raw = file?.raw || file
  if (!raw) return
  proofBusy.value = docType
  try {
    const fd = new FormData()
    fd.append('files', raw)
    fd.append('doc_type', docType)
    const { data } = await projectApi.upload(props.p.id, fd)
    const bad = (data.results || []).find((r) => !r.ok)
    if (bad) ElMessage.warning(`《${bad.filename}》未收下：${bad.message}`)
    else ElMessage.success('专家验收意见已备案')
    emit('refresh')
  } catch (e) {
    showError(e?.response?.data?.detail || '上传失败，请稍后重试')
  } finally {
    proofBusy.value = ''
  }
}

async function removeProof(row) {
  try {
    await ElMessageBox.confirm(`确认删除《${row.filename}》？`, '删除验收材料',
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

const openRaw = (row) => emit('open-raw', row)
</script>

<template>
<div v-if="['acceptance', 'accepted'].includes(p.stage)" class="proof-box">
  <div class="proof-head">
    <span class="proof-title">{{ p.stage === 'acceptance' ? '验收材料' : '结项备案' }}</span>
    <span class="proof-sub">
      {{ p.stage === 'acceptance'
        ? '按模板出具，盖章后送建设单位；没有这份证明不能验收'
        : '专家验收意见在结项后补传备案，不影响结项' }}
    </span>
  </div>

  <!-- 待验收：证明是生成出来的，不是收上来的 -->
  <template v-if="p.stage === 'acceptance'">
    <div class="proof-row">
      <div class="proof-label">
        <span class="proof-name">数据归集证明</span>
        <span class="proof-tag" :class="collectDoc ? 'ok' : 'need'">
          {{ collectDoc ? '已出具' : '缺此不能验收' }}
        </span>
      </div>
      <div class="proof-files">
        <div v-if="collectDoc" class="proof-file">
          <el-button link type="primary" class="doc-name"
            @click="downloadDoc(collectDoc)">{{ collectDoc.name }}</el-button>
          <span class="report-meta">
            {{ collectDoc.created_by_name }} · {{ fmtTime(collectDoc.created) }}
          </span>
          <span v-if="collectDoc.meta?.doc_no" class="report-meta">
            编号 {{ collectDoc.meta.doc_no }}
          </span>
        </div>
        <template v-else>
          <div class="proof-src">
            <div class="proof-src-h">试运行情况（申报单位填报）</div>
            <div class="proof-src-b">{{ p.trial_summary || '未填报' }}</div>
          </div>
          <div class="proof-src">
            <div class="proof-src-h">数据归集情况（申报单位填报）</div>
            <div class="proof-src-b">{{ p.data_collect_scope || '未填报' }}</div>
          </div>
        </template>
      </div>
      <div v-if="canIssueProof" class="proof-act">
        <el-button size="small" type="primary" @click="openIssue">
          {{ collectDoc ? '重新出具' : '出具证明' }}
        </el-button>
      </div>
    </div>
    <div v-if="!canIssueProof" class="proof-empty proof-readonly">
      《数据归集证明》由我局（审核岗）按模板出具，建设单位只查看。
    </div>
  </template>

  <!-- 已验收：只收专家验收意见 -->
  <div v-else class="proof-row">
    <div class="proof-label">
      <span class="proof-name">专家验收意见</span>
      <span class="proof-tag" :class="acceptOpinions.length ? 'ok' : 'need'">
        {{ acceptOpinions.length ? '已备案' : '待上传' }}
      </span>
    </div>
    <div class="proof-files">
      <div v-for="r in acceptOpinions" :key="r.id" class="proof-file">
        <el-button link type="primary" class="doc-name" @click="openRaw(r)">
          {{ r.filename }}
        </el-button>
        <span class="report-meta">{{ r.uploaded_by_name }} · {{ fmtTime(r.created) }}</span>
        <el-button v-if="canFileOpinion" link type="danger" size="small"
          @click="removeProof(r)">删除</el-button>
      </div>
      <span v-if="!acceptOpinions.length" class="proof-empty">
        验收会结束后把专家意见（签字页扫描件即可）传上来备案。
      </span>
    </div>
    <el-upload v-if="canFileOpinion" :auto-upload="false" :show-file-list="false"
      accept=".pdf,.docx,.jpg,.jpeg,.png" :on-change="(f) => uploadProof(f, 'accept_opinion')">
      <el-button size="small" :loading="proofBusy === 'accept_opinion'">上传</el-button>
    </el-upload>
  </div>
</div>

<!-- 出具《数据归集证明》：两段正文摊开可改，改过的回写项目 -->
<el-dialog v-model="issueDlg" title="出具《数据归集证明》" width="720px" top="8vh">
  <el-alert type="info" :closable="false" show-icon class="confirm-alert"
    title="两段正文会原样进证明。表述不清的先「退回补充试运行与归集说明」，别在这里替单位写。" />
  <el-form label-position="top">
    <el-form-item label="证明编号">
      <el-input v-model="issueForm.doc_no" style="max-width: 280px"
        :placeholder="`留空按 SJH-${p?.code || ''} 生成`" />
    </el-form-item>
    <el-form-item label="试运行情况">
      <el-input v-model="issueForm.trial_summary" type="textarea" :rows="4"
        placeholder="试运行起止时间、运行是否稳定、遗留问题" />
    </el-form-item>
    <el-form-item label="数据归集情况">
      <el-input v-model="issueForm.data_collect_scope" type="textarea" :rows="4"
        placeholder="归集了哪些数据：系统、库表、数据量、归集方式" />
    </el-form-item>
  </el-form>
  <template #footer>
    <el-button @click="issueDlg = false">取消</el-button>
    <el-button type="primary" :loading="docBusy" @click="genDataCollect">
      生成并下载
    </el-button>
  </template>
</el-dialog>
</template>

<style scoped>
@import '../base.css';

/* 待验收的两份材料：归集证明是前置条件，专家意见是备案件。
   用两行固定结构，而不是一个上传框 —— 缺哪一份要一眼看得出 */
.proof-box {
  margin-top: 14px;
  padding: 12px 14px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--panel-2);
}

.proof-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.proof-title {
  font-size: 13px;
  font-weight: 500;
}

.proof-sub {
  font-size: 11.5px;
  color: var(--ink-4);
}

.proof-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 12px 0;
  border-top: 1px dashed var(--line-soft);
}

.proof-label {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 178px;
  flex-shrink: 0;
}

.proof-name {
  font-size: 12.5px;
  color: var(--ink-2);
}

.proof-tag {
  font-size: 12px;
  line-height: 17px;
  padding: 0 7px;
  border-radius: 4px;
}

.proof-tag.ok {
  background: var(--success-soft, #e8f5ee);
  color: var(--success, #1d6b4f);
}

.proof-tag.need {
  background: var(--warn-bg, #fdf6e7);
  color: var(--warn, #b25f00);
}

.proof-files {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.proof-file {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.proof-empty {
  font-size: 11.5px;
  color: var(--ink-4);
  line-height: 1.8;
}

.proof-readonly {
  margin-top: 6px;
}

/* 出具前的依据：单位报的试运行与归集情况，缩进一条竖线，看着像引用 */
.proof-src {
  border-left: 2px solid var(--line);
  padding-left: 10px;
  margin-bottom: 8px;
}

.proof-src-h {
  font-size: 12px;
  color: var(--ink-4);
  letter-spacing: 0.04em;
}

.proof-src-b {
  font-size: 12.5px;
  color: var(--ink-2);
  line-height: 1.8;
  margin-top: 2px;
  white-space: pre-wrap;
}

.proof-act {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
</style>
