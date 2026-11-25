<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { orgApi, roleApi, userApi } from '@/api'

const loading = ref(false)
const rows = ref([])
const dialog = ref(false)
const saving = ref(false)
const editingId = ref(null)
// 后端分页默认 20 条，不翻页的话第 21 个以后的账号在页面上根本管不到
const page = ref(1)
const size = ref(20)
const total = ref(0)

// 角色不再是写死的四个：管理员可以在「系统设置 → 角色」里加人加岗，
// 这里只负责把角色表拉下来供选择
const roles = ref([])

async function loadRoles() {
  try {
    const { data } = await roleApi.list()
    roles.value = data.results || data || []
  } catch (e) {
    roles.value = []
  }
}

const defaultRoleId = computed(() => (roles.value.find((r) => r.scope === 'applicant')
  || roles.value[0] || {}).id || null)

const form = reactive({
  username: '',
  name: '',
  role_id: null,
  unit: '',
  phone: '',
  password: '',
  is_active: true,
})

const isEdit = computed(() => !!editingId.value)

// 内置角色的名称与权限域同名，不必重复写成「初审岗（初审岗）」；
// 只有自定义角色才补一个域，说明它属于哪一类
function roleLabel(r) {
  return r.name === r.scope_label ? r.name : `${r.name}（${r.scope_label}）`
}

// 账号单位与申报单位共用一份名录：两边写法不一致，按单位统计就对不上
const orgGroups = ref([])

async function loadOrgUnits() {
  try {
    const { data } = await orgApi.units()
    orgGroups.value = data.groups || []
  } catch (e) {
    orgGroups.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const { data } = await userApi.list({ page: page.value, page_size: size.value })
    rows.value = data.results || data
    total.value = data.count ?? rows.value.length
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, {
    username: '', name: '', role_id: defaultRoleId.value, unit: '', phone: '',
    password: '', is_active: true,
  })
  dialog.value = true
}

function openEdit(row) {
  editingId.value = row.id
  Object.assign(form, {
    username: row.username,
    name: row.name,
    role_id: row.role_id,
    unit: row.unit || '',
    phone: row.phone || '',
    password: '',
    is_active: row.is_active,
  })
  dialog.value = true
}

async function submit() {
  if (!form.username.trim() || !form.name.trim()) {
    ElMessage.warning('用户名与姓名必填')
    return
  }
  if (!form.role_id) {
    ElMessage.warning('请选择角色')
    return
  }
  if (!isEdit.value && form.password.length < 8) {
    ElMessage.warning('初始密码至少 8 位')
    return
  }
  saving.value = true
  try {
    if (isEdit.value) {
      await userApi.update(editingId.value, {
        username: form.username.trim(),
        name: form.name.trim(),
        role_id: form.role_id,
        unit: form.unit,
        phone: form.phone,
        is_active: form.is_active,
      })
      ElMessage.success('已保存')
    } else {
      await userApi.create({
        username: form.username.trim(), name: form.name.trim(), role_id: form.role_id,
        unit: form.unit, phone: form.phone, password: form.password,
      })
      ElMessage.success('账号已创建，首次登录会提示改密')
    }
    dialog.value = false
    load()
  } catch { /* 错误提示由拦截器统一弹出 */ } finally {
    saving.value = false
  }
}

/* ------------------------------------------------------------ 重置密码 */
const pwdDialog = ref(false)
const pwdSaving = ref(false)
const pwdTarget = ref(null)
const pwdForm = reactive({ password: '' })

function openReset(row) {
  pwdTarget.value = row
  pwdForm.password = ''
  pwdDialog.value = true
}

async function submitReset() {
  if (pwdForm.password.length < 8) {
    ElMessage.warning('新密码至少 8 位')
    return
  }
  pwdSaving.value = true
  try {
    const { data } = await userApi.resetPassword(pwdTarget.value.id, {
      password: pwdForm.password,
    })
    pwdDialog.value = false
    ElMessage.success(data?.detail || '密码已重置')
  } catch { /* 错误提示由拦截器统一弹出 */ } finally {
    pwdSaving.value = false
  }
}

/* ------------------------------------------------------------ 启停 */
async function disable(row) {
  try {
    await ElMessageBox.confirm(
      `停用「${row.display}」后该账号将无法登录，历史留痕保留。确认停用？`,
      '停用账号',
      { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
    )
    await userApi.remove(row.id)
    ElMessage.success('已停用')
    load()
  } catch { /* 取消 */ }
}

async function enable(row) {
  try {
    await userApi.update(row.id, { is_active: true })
    ElMessage.success('已启用，可正常登录')
    load()
  } catch { /* 错误提示由拦截器统一弹出 */ }
}

onMounted(() => {
  loadRoles()
  load()
  loadOrgUnits()
})
</script>

<template>
  <div class="users-page">
    <div class="bar">
      <div>
        <div class="bar-count">共 <b>{{ total }}</b> 个账号</div>
        <div class="bar-sub">账号由管理员创建，不开放自助注册</div>
      </div>
      <el-button type="primary" @click="openCreate">新建账号</el-button>
    </div>

    <div class="table-card">
      <el-table v-loading="loading" :data="rows" style="width: 100%">
        <el-table-column prop="username" label="用户名" width="120">
          <template #default="{ row }">
            <span class="mono">{{ row.username }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="姓名" width="110" />
        <el-table-column label="角色" width="176">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.role_label }}</el-tag>
            <span v-if="row.role_label !== row.scope_label" class="role-scope">
              {{ row.scope_label }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="unit" label="单位" min-width="180" />
        <el-table-column prop="phone" label="手机" width="130">
          <template #default="{ row }">
            <span class="mono">{{ row.phone || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small" effect="light">
              {{ row.is_active ? '正常' : '已停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="196" align="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" size="small" @click="openReset(row)">重置密码</el-button>
            <el-button
              v-if="row.is_active"
              link
              type="danger"
              size="small"
              @click="disable(row)"
            >停用</el-button>
            <el-button v-else link type="success" size="small" @click="enable(row)">启用</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="table-foot">
        <span class="note-inline">
          密码只存哈希，管理员也看不到明文；忘记时用「重置密码」。
        </span>
        <el-pagination
          v-model:current-page="page"
          :page-size="size"
          :total="total"
          layout="prev, pager, next"
          @current-change="load"
        />
      </div>
    </div>


    <el-dialog v-model="dialog" :title="isEdit ? '编辑账号' : '新建账号'" width="460px">
      <el-form label-position="top">
        <el-form-item label="用户名（登录用）">
          <el-input v-model="form.username" placeholder="如 zhangsan" />
          <div v-if="isEdit" class="field-tip">
            改用户名后，对方须用新用户名登录；已有的流转记录不受影响。
          </div>
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="form.name" placeholder="真实姓名" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role_id" style="width: 100%"
            placeholder="选择角色（可到系统设置里新建）">
            <el-option
              v-for="r in roles"
              :key="r.id"
              :label="roleLabel(r)"
              :value="r.id"
              :disabled="!r.active"
            >
              <span>{{ roleLabel(r) }}</span>
              <span v-if="!r.active" class="opt-off">已停用</span>
            </el-option>
          </el-select>
          <div class="field-tip">
            角色决定能办哪些环节；需要新岗位到「系统设置 → <b>角色管理</b>」新建。
          </div>
        </el-form-item>
        <el-form-item label="单位">
          <el-select
            v-model="form.unit"
            filterable
            clearable
            placeholder="从市本级单位名录中选择"
            style="width: 100%"
          >
            <el-option-group v-for="g in orgGroups" :key="g.key" :label="g.label">
              <el-option v-for="u in g.items" :key="u.id" :label="u.name" :value="u.name" />
            </el-option-group>
          </el-select>
        </el-form-item>
        <el-form-item label="手机">
          <el-input v-model="form.phone" placeholder="选填" />
        </el-form-item>
        <el-form-item v-if="isEdit" label="状态">
          <el-switch
            v-model="form.is_active"
            active-text="可登录"
            inactive-text="已停用"
          />
        </el-form-item>
        <el-form-item v-else label="初始密码（至少 8 位）">
          <el-input v-model="form.password" show-password placeholder="首次登录后自行修改" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">
          {{ isEdit ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="pwdDialog" title="重置密码" width="420px">
      <div class="pwd-target">
        账号：<b>{{ pwdTarget?.display }}</b>
        <span class="muted">（{{ pwdTarget?.username }}）</span>
      </div>
      <el-form label-position="top">
        <el-form-item label="新密码（至少 8 位）">
          <el-input
            v-model="pwdForm.password"
            show-password
            placeholder="设一个临时密码，告知对方后由其自行修改"
          />
        </el-form-item>
      </el-form>
      <div class="field-tip">
        重置后对方已登录的设备会立即掉线，下次登录须重新设置密码。
      </div>
      <template #footer>
        <el-button @click="pwdDialog = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="submitReset">确认重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.users-page { max-width: 1480px; }

.bar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
.bar-count { font-size: 14px; color: var(--ink-2); }
.bar-count b { font-weight: 600; font-size: 17px; color: var(--ink); margin: 0 2px; }
.bar-sub { font-size: 11.5px; color: var(--ink-4); margin-top: 2px; }

.table-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}
.table-card :deep(th.el-table__cell) { background: var(--panel-2); }

.table-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-top: 1px solid var(--line-soft);
  background: var(--panel-2);
}
.note-inline { font-size: 11.5px; color: var(--ink-4); line-height: 1.6; }

.note {
  font-size: 11.5px;
  color: var(--ink-3);
  margin-top: 14px;
  line-height: 1.9;
  max-width: 900px;
}

.field-tip {
  font-size: 11.5px;
  line-height: 1.8;
  color: var(--ink-3);
  margin-top: 4px;
}

.pwd-target {
  font-size: 13px;
  color: var(--ink-2);
  margin-bottom: 14px;
}

.muted { color: var(--ink-3); font-size: 12px; }

.role-scope { font-size: 11.5px; color: var(--ink-4); margin-left: 6px; }
.opt-off { font-size: 11.5px; color: var(--ink-4); margin-left: 8px; }
</style>
