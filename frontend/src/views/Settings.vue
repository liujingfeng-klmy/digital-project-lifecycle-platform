<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { llmApi, roleApi, slaApi } from '@/api'

const loading = ref(true)
const saving = ref(false)
const testing = ref(false)
const presets = ref([])
const config = ref(null)
const testResult = ref(null)

const form = reactive({
  preset: 'deepseek',
  base_url: '',
  model: '',
  api_key: '',
  temperature: 0.2,
  enabled: false,
  skip_ai: true,
})

const keyMask = computed(() => config.value?.llm?.api_key || '')
const keyConfigured = computed(() => !!config.value?.llm?.api_key_set)

const currentPreset = computed(() =>
  presets.value.find((p) => p.key === form.preset) || null,
)

const connState = computed(() => {
  if (!form.base_url) return { type: 'info', text: '未填写模型地址' }
  if (keyConfigured.value || form.api_key) return { type: 'success', text: '已填写地址与密钥，建议测试连接' }
  return { type: 'warning', text: '地址已填，密钥未填（Ollama 等本地模型可留空）' }
})

const slaItems = ref([])
const slaSaving = ref(false)

async function loadSla() {
  try {
    const { data } = await slaApi.list()
    slaItems.value = (data.items || []).map((x) => ({ ...x }))
  } catch (e) {
    slaItems.value = []
  }
}

async function saveSla() {
  slaSaving.value = true
  try {
    const { data } = await slaApi.save(
      slaItems.value.map((x) => ({
        stage: x.stage, label: x.label, days: x.days, role: x.role || '',
      })),
    )
    ElMessage.success(data.detail || '已保存')
    await loadSla()
  } finally {
    slaSaving.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const [p, c] = await Promise.all([llmApi.presets(), llmApi.config()])
    presets.value = p.data.presets || []
    config.value = c.data
    const llm = c.data.llm || {}
    form.preset = llm.provider || 'deepseek'
    form.base_url = llm.base_url || ''
    form.model = llm.model || ''
    form.temperature = llm.temperature ?? 0.2
    form.enabled = !!llm.enabled
    form.skip_ai = c.data.sensitive?.skip_ai !== false
  } finally {
    loading.value = false
  }
}

function applyPreset(key) {
  const p = presets.value.find((x) => x.key === key)
  if (!p) return
  form.base_url = p.base_url || ''
  form.model = p.model || ''
  testResult.value = null
}

async function handleTest() {
  if (!form.base_url || !form.model) {
    ElMessage.warning('请先填写模型地址与模型名称')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const payload = { base_url: form.base_url, model: form.model }
    if (form.api_key) payload.api_key = form.api_key
    const { data } = await llmApi.test(payload)
    testResult.value = { ok: true, ...data }
    ElMessage.success(`连接正常，响应 ${data.latency_ms} 毫秒`)
  } catch (e) {
    const d = e.response?.data || {}
    testResult.value = { ok: false, error: d.error || '测试失败' }
  } finally {
    testing.value = false
  }
}

async function handleSave() {
  saving.value = true
  try {
    const llm = {
      provider: form.preset,
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      temperature: Number(form.temperature),
      enabled: form.enabled,
    }
    if (form.api_key) llm.api_key = form.api_key

    const { data } = await llmApi.save({
      llm,
      sensitive: { skip_ai: form.skip_ai },
    })
    config.value = data
    form.api_key = ''
    ElMessage.success('已写入服务器配置文件 config.json')
  } finally {
    saving.value = false
  }
}

/* ------------------------------------------------------------ 角色管理
 * 角色从代码里搬进库里之后，管理员在这里维护：加一个「初审岗（数据）」，
 * 或者把一个岗位停用。权限域决定这个角色能办哪些环节，不另配权限项 ——
 * 这套系统的权限本来就只有「在哪个环节能动手」这一层。
 */
const scopes = [
  { value: 'applicant', label: '申报单位', hint: '申报、补正、反馈意见' },
  { value: 'reviewer', label: '初审岗', hint: '预审、评审会、立项、招标与建设跟进' },
  { value: 'auditor', label: '审核岗', hint: '费用评审、财政审核、验收归档' },
  { value: 'admin', label: '管理员', hint: '账号、角色、模板、时限与全量台账' },
]
const roleRows = ref([])
const roleLoading = ref(false)
const roleDialog = ref(false)
const roleSaving = ref(false)
const roleEditing = ref(null)
const roleForm = reactive({ name: '', scope: 'reviewer', description: '', active: true })

async function loadRoles() {
  roleLoading.value = true
  try {
    const { data } = await roleApi.list()
    roleRows.value = data.results || data || []
  } finally {
    roleLoading.value = false
  }
}

function openRole(row) {
  roleEditing.value = row || null
  Object.assign(roleForm, {
    name: row?.name || '',
    scope: row?.scope || 'reviewer',
    description: row?.description || '',
    active: row ? row.active : true,
  })
  roleDialog.value = true
}

async function saveRole() {
  if (!roleForm.name.trim()) {
    ElMessage.warning('请填写角色名称')
    return
  }
  roleSaving.value = true
  try {
    const payload = {
      name: roleForm.name.trim(),
      scope: roleForm.scope,
      description: roleForm.description.trim(),
      active: roleForm.active,
    }
    if (roleEditing.value) await roleApi.update(roleEditing.value.id, payload)
    else await roleApi.create(payload)
    ElMessage.success(roleEditing.value ? '角色已保存' : '角色已创建，可在账号管理里分配')
    roleDialog.value = false
    await loadRoles()
  } catch { /* 错误提示由拦截器统一弹出 */ } finally {
    roleSaving.value = false
  }
}

async function toggleRole(row) {
  try {
    await roleApi.update(row.id, { active: !row.active })
    ElMessage.success(row.active ? '已停用' : '已启用')
    await loadRoles()
  } catch { /* 错误提示由拦截器统一弹出 */ }
}

async function removeRole(row) {
  try {
    await ElMessageBox.confirm(
      `删除角色「${row.name}」？只有在它下面没有账号时才能删除，`
      + '否则请先把账号改到其他角色。',
      '删除角色',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await roleApi.remove(row.id)
    ElMessage.success('已删除')
    await loadRoles()
  } catch { /* 取消或后端拒绝 */ }
}

onMounted(() => {
  load()
  loadSla()
  loadRoles()
})
</script>

<template>
  <div v-loading="loading">
    <div class="top-grid">
      <el-card shadow="never" class="card-fill">
          <template #header>
            <div class="card-head">
              <span class="card-title">模型接入</span>
              <el-tag :type="keyConfigured ? 'success' : 'info'" size="small" effect="light">
                {{ keyConfigured ? '已配置' : '未配置' }}
              </el-tag>
            </div>
          </template>

          <el-form label-position="top">
            <el-form-item label="服务提供方">
              <el-select v-model="form.preset" style="width: 100%" @change="applyPreset">
                <el-option
                  v-for="p in presets"
                  :key="p.key"
                  :label="p.label"
                  :value="p.key"
                >
                  <span>{{ p.label }}</span>
                  <span class="opt-note">{{ p.note }}</span>
                </el-option>
              </el-select>
              <div v-if="currentPreset" class="hint">{{ currentPreset.note }}</div>
            </el-form-item>

            <el-form-item label="模型地址（base_url）">
              <el-input v-model="form.base_url" placeholder="https://api.deepseek.com/v1" />
              <div class="hint">填到 <code>/v1</code> 为止；换内网只改这一行。</div>
            </el-form-item>

            <el-form-item label="模型名称（model）">
              <el-input v-model="form.model" placeholder="deepseek-flash" />
              <div class="hint">
                填接口的模型 id：<code>deepseek-flash</code>（V4.1-Flash）或
                <code>deepseek-v4-pro</code>，不是宣传名。
              </div>
            </el-form-item>

            <el-form-item label="API 密钥">
              <el-input
                v-model="form.api_key"
                type="password"
                show-password
                :placeholder="keyConfigured ? `已保存 ${keyMask}，留空表示不修改` : '粘贴密钥，仅存服务器'"
              />
              <div class="hint">只存服务器 <code>config.json</code>，接口不返回明文。</div>
            </el-form-item>

            <el-form-item label="温度（temperature）">
              <el-slider v-model="form.temperature" :min="0" :max="1" :step="0.1" show-input />
              <div class="hint">审查类任务建议 0.1—0.3，越低越稳定、越少发挥。</div>
            </el-form-item>
          </el-form>

          <div class="actions">
            <el-button :loading="testing" @click="handleTest">测试连接</el-button>
            <el-button type="primary" :loading="saving" @click="handleSave">保存配置</el-button>
            <el-tag :type="connState.type" size="small" effect="plain" class="state-tag">
              {{ connState.text }}
            </el-tag>
          </div>

          <div v-if="testResult" class="test-result" :class="testResult.ok ? 'ok' : 'bad'">
            <template v-if="testResult.ok">
              连接成功 · 响应 {{ testResult.latency_ms }} ms · 模型回复「{{ testResult.reply }}」
            </template>
            <template v-else>
              连接失败：{{ testResult.error }}
            </template>
          </div>
        </el-card>

      <div class="side-stack">
        <el-card shadow="never">
          <template #header><span class="card-title">审查策略</span></template>
          <el-form label-position="top">
            <el-form-item label="涉敏材料">
              <el-switch v-model="form.skip_ai" active-text="跳过 AI 审查" />
              <div class="hint">
                开启后，标记为涉敏的项目只跑规则预审，材料不出网。
              </div>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card shadow="never" class="card-grow">
          <template #header><span class="card-title">切换方式</span></template>
          <div class="steps-note">
            <div class="sn-row">
              <span class="sn-no">1</span>
              <div class="sn-body">
                <b>换商用模型</b>
                <p>选预设、填密钥、保存，立即生效。</p>
              </div>
            </div>
            <div class="sn-row">
              <span class="sn-no">2</span>
              <div class="sn-body">
                <b>切内网模型</b>
                <p>选「单位内网」，填网关地址与模型名。</p>
              </div>
            </div>
            <div class="sn-row">
              <span class="sn-no">3</span>
              <div class="sn-body">
                <b>完全离线</b>
                <p>本机装 Ollama 拉模型，选「本机 Ollama」，密钥留空。</p>
              </div>
            </div>
          </div>
        </el-card>
      </div>
    </div>

    <!-- 环节审查时限：管理员统一设定，办理人在详情页看到「剩余 X 天 / 已超期 X 天」 -->
    <el-card shadow="never" class="sla-card">
      <template #header>
        <div class="card-head">
          <span class="card-title">环节时限与办理岗位</span>
          <span class="sla-sub">按自然日计，进入环节当天算第 1 天；填 0 表示该环节不计时</span>
        </div>
      </template>
      <div class="sla-grid">
        <div v-for="it in slaItems" :key="it.stage" class="sla-item">
          <div class="sla-name">{{ it.label }}</div>
          <el-input-number v-model="it.days" :min="0" :max="365" size="small"
            controls-position="right" style="width: 84px" />
          <span class="sla-unit">天</span>
          <!-- 环节办理岗位：默认按权限域兜底，可以指到域内的具体岗位。
               立项、招标这类环节各位局里归谁办不一样，不能写死在代码里 -->
          <el-select v-model="it.role" size="small" class="sla-role"
            placeholder="按权限域">
            <el-option label="按权限域" value="" />
            <el-option v-for="r in (it.role_options || [])" :key="r.code"
              :label="r.name" :value="r.code" />
          </el-select>
          <div class="sla-meta">
            在办 {{ it.active }} 件
            <span v-if="it.days !== it.default" class="sla-diff">默认 {{ it.default }}</span>
            <span v-if="it.role" class="sla-role-tag">
              {{ it.role_label }}办理
            </span>
          </div>
        </div>
      </div>
      <div class="actions">
        <el-button type="primary" :loading="slaSaving" @click="saveSla">保存设置</el-button>
        <span class="sla-tip">
          时限改动只影响正在办的项目剩余天数，不追溯已经办完的环节。
          岗位只能在同一权限域里选：跨域指定会让这个环节的办理按钮在对方手里不出现。
        </span>
      </div>
    </el-card>

    <!-- 角色：与账号分开维护。一个岗位可以有多个人，转出时按人指派 -->
    <el-card shadow="never" class="role-card">
      <template #header>
        <div class="card-head">
          <span class="card-title">角色管理</span>
          <span class="sla-sub">
            先建角色，再到「账号管理」里把账号挂上去；同一角色可以有多个人，
            转出时再指定由谁承办
          </span>
          <el-button type="primary" size="small" class="head-btn" @click="openRole()">
            新建角色
          </el-button>
        </div>
      </template>
      <el-table v-loading="roleLoading" :data="roleRows" style="width: 100%">
        <el-table-column prop="name" label="角色名称" min-width="150">
          <template #default="{ row }">
            <span class="role-name">{{ row.name }}</span>
            <el-tag v-if="row.builtin" size="small" effect="plain" class="role-badge">内置</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="权限域" width="140">
          <template #default="{ row }">
            <el-tag size="small" effect="light">{{ row.scope_label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="账号" width="90">
          <template #default="{ row }">{{ row.user_count }} 人</template>
        </el-table-column>
        <el-table-column prop="description" label="说明" min-width="220">
          <template #default="{ row }">
            <span class="role-desc">{{ row.description || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.active ? 'success' : 'info'" size="small" effect="light">
              {{ row.active ? '启用' : '已停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" align="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openRole(row)">编辑</el-button>
            <el-button link :type="row.active ? 'warning' : 'success'" size="small"
              @click="toggleRole(row)">{{ row.active ? '停用' : '启用' }}</el-button>
            <el-button v-if="!row.builtin" link type="danger" size="small"
              @click="removeRole(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="role-tip">
        权限域决定这个角色能办哪些环节，不再单独配权限项；
        内置角色可以改名，不能删、不能改权限域 —— 历史留痕里存的是它们的代码。
      </div>
    </el-card>

    <el-dialog v-model="roleDialog" :title="roleEditing ? '编辑角色' : '新建角色'" width="460px">
      <el-form label-position="top">
        <el-form-item label="角色名称">
          <el-input v-model="roleForm.name" placeholder="如：初审岗（政务）、审核岗（数据）" />
        </el-form-item>
        <el-form-item label="权限域">
          <el-select v-model="roleForm.scope" style="width: 100%"
            :disabled="!!roleEditing?.builtin">
            <el-option v-for="s in scopes" :key="s.value" :label="s.label" :value="s.value">
              <span>{{ s.label }}</span>
              <span class="opt-hint">{{ s.hint }}</span>
            </el-option>
          </el-select>
          <div class="hint">
            {{ roleEditing?.builtin
              ? '内置角色的权限域不可改；要换域请新建角色，再把账号迁过去。'
              : '决定这个角色能办哪些环节：申报、初审、审核或系统管理。' }}
          </div>
        </el-form-item>
        <el-form-item label="说明（选填）">
          <el-input v-model="roleForm.description" placeholder="写清这个岗位负责什么，便于分配账号" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="roleForm.active" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="roleDialog = false">取消</el-button>
        <el-button type="primary" :loading="roleSaving" @click="saveRole">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 顶部两栏：左表单、右两卡。两栏等高、底边齐平 —— 之前各栏各自堆叠，
   右栏比左栏高出一截，下面这张通栏卡片就卡在半空，看着像没对齐。 */
.top-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.62fr) minmax(0, 1fr);
  align-items: stretch;
  gap: 16px;
  margin-bottom: 16px;
}

.side-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

/* 左卡：表单把卡片撑满，操作条落到卡片底部，与右栏最后一行齐平 */
.card-fill { display: flex; flex-direction: column; }
.card-fill :deep(.el-card__body) {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
}
.card-fill .actions { margin-top: auto; }

/* 右卡末位：反过来若右栏偏矮，由它补足高度，两栏底边始终一条线 */
.card-grow { display: flex; flex-direction: column; }
.card-grow :deep(.el-card__body) { flex: 1 1 auto; }

.role-card { margin-top: 16px; }
.head-btn { margin-left: auto; }
.role-name { font-weight: 500; }
.role-badge { margin-left: 6px; }
.role-desc { color: var(--ink-3); font-size: 12.5px; }
.role-tip {
  margin-top: 10px;
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--ink-4);
}
.opt-hint { font-size: 11.5px; color: var(--ink-4); margin-left: 8px; }

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.card-title { font-size: 15px; font-weight: 600; }

/* 表单里说明文字压到 11.5px，标题与控件之间留出呼吸 */
:deep(.el-form-item) { margin-bottom: 18px; }
:deep(.el-form-item__label) {
  font-size: 12.5px;
  color: var(--ink-3);
  padding-bottom: 4px;
}

.hint {
  font-size: 11.5px;
  color: var(--ink-4);
  line-height: 1.7;
  margin-top: 4px;
}

.hint code {
  font-family: var(--font-mono);
  background: var(--panel-3);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
  color: var(--ink-2);
}

.opt-note {
  float: right;
  font-size: 12px;
  color: var(--ink-4);
  margin-left: 12px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}

.state-tag { margin-left: auto; }

.test-result {
  margin-top: 12px;
  padding: 10px 13px;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.7;
}

.test-result.ok { background: var(--ok-bg); color: var(--ok); }
.test-result.bad { background: var(--danger-bg); color: var(--danger); }

/* 三种切换方式：编号小圆点 + 标题 + 一句说明 */
.steps-note { display: flex; flex-direction: column; }
.sn-row {
  display: flex;
  gap: 11px;
  padding: 10px 0;
  border-bottom: 1px dashed var(--line-soft);
}
.sn-row:first-child { padding-top: 2px; }
.sn-row:last-child { border-bottom: 0; }
.sn-no {
  flex: 0 0 20px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--brand-bg);
  color: var(--brand-deep);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}
.sn-body b { font-size: 12.5px; font-weight: 500; color: var(--ink); }
.sn-body p { margin: 2px 0 0; font-size: 11.5px; color: var(--ink-3); line-height: 1.7; }
.sn-foot {
  margin: 16px 0 0;
  font-size: 11.5px;
  color: var(--ink-4);
  line-height: 1.7;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}

.note { font-size: 12.5px; color: var(--ink-2); line-height: 1.9; }
.note p { margin: 0 0 6px; }
.note .muted { color: var(--ink-3); font-size: 11.5px; margin-top: 10px; }

/* 环节时限：一行 4 个，窄屏自动折行 */
.sla-sub { font-size: 11.5px; color: var(--ink-4); margin-left: auto; }
.sla-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 18px;
}
.sla-item {
  display: grid;
  grid-template-columns: 1fr auto auto;
  font-variant-numeric: tabular-nums;
  align-items: center;
  gap: 4px 6px;
  padding: 9px 11px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--panel-2);
}
.sla-name { font-size: 12.5px; color: var(--ink); }
.sla-unit { font-size: 11.5px; color: var(--ink-3); }
.sla-meta {
  grid-column: 1 / -1;
  font-size: 12px;
  color: var(--ink-4);
  display: flex;
  gap: 8px;
}
.sla-diff { color: var(--warn, #B26A00); }
.sla-role { grid-column: 1 / -1; width: 100%; }
.sla-role-tag { color: var(--brand-deep); }
.sla-tip { font-size: 11.5px; color: var(--ink-4); }
@media (max-width: 1200px) {
  .sla-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 1040px) {
  .top-grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
