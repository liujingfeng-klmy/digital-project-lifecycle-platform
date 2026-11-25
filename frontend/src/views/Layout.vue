<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
// 待办数挂在侧栏上，用户不用进首页也知道有几件等自己办。
// 放全局 store：详情页标记已读后能直接刷新这个数字
const ui = useUiStore()
// 构建时间由 vite 打进包里；侧栏底部显示，用来分辨当前是不是最新版本
const buildAt = __BUILD_AT__

/* ------------------------------------------------------------ 修改密码 */
// 首次登录（或管理员重置后）必须改密：弹窗置顶且不可关闭。
// 原来后端置了 must_change_password，前端一处都没读，等于没做。
const pwdShow = ref(false)
const pwdSaving = ref(false)
const pwdForm = reactive({ old_password: '', new_password: '', confirm: '' })
const mustChange = computed(() => !!userStore.user?.must_change_password)

function openPwd() {
  Object.assign(pwdForm, { old_password: '', new_password: '', confirm: '' })
  pwdShow.value = true
}

async function submitPwd() {
  if (!pwdForm.old_password) {
    ElMessage.warning('请填写原密码')
    return
  }
  if (pwdForm.new_password.length < 8) {
    ElMessage.warning('新密码至少 8 位')
    return
  }
  if (pwdForm.new_password !== pwdForm.confirm) {
    ElMessage.warning('两次输入的新密码不一致')
    return
  }
  if (pwdForm.new_password === pwdForm.old_password) {
    ElMessage.warning('新密码不能与原密码相同')
    return
  }
  pwdSaving.value = true
  try {
    await userStore.changePassword({
      old_password: pwdForm.old_password,
      new_password: pwdForm.new_password,
    })
    pwdShow.value = false
    ElMessage.success('密码已修改，其他设备上的登录已失效')
  } catch { /* 错误提示由拦截器统一处理 */ } finally {
    pwdSaving.value = false
  }
}

onMounted(() => {
  ui.refreshPending()
  if (mustChange.value) {
    pwdShow.value = true
    ElMessage.warning('首次登录须修改初始密码后才能继续使用')
  }
})
/* 滚动容器是 .view 而不是 window，切页必须手动回顶，
   否则从长列表点进详情会停在中间，看着像页面丢了开头 */
const viewEl = ref(null)
watch(() => route.fullPath, () => {
  ui.refreshPending()
  nextTick(() => { if (viewEl.value) viewEl.value.scrollTop = 0 })
})

/* 导航按模板分三组：工作台 / 业务办理 / 系统管理。
   每组里放谁仍按角色能看到的范围决定，组本身不随角色增减。 */
const navGroups = computed(() => {
  // 在办箱与待办并列放在「工作台」组里：两者都是「我自己的件」，
  // 一个还没动手、一个填了一半。放在「已办项目」上方，是因为它比项目台账
  // 更常用 —— 手上有活的时候，回来接着填的就是这里
  const work = [
    { name: 'dashboard', label: '待办工作台', hint: '按角色列出', badge: ui.pending },
    { name: 'inbox', label: '在办箱', hint: '点开过的件，接着办', badge: ui.doing },
  ]
  // 这一页所有角色都叫「已办项目」：申报人看到的是自己报过的件，
  // 其余角色看到的是自己经办过的件。名字跟页内「返回已办项目」一致，
  // 不按角色换叫法 —— 同一条路径出现两个名字，人会以为进了另一个地方
  const biz = [
    { name: 'projects', label: '已办项目', hint: userStore.role === 'applicant' ? '我报过的件' : '我经手的' },
  ]
  /* 储备库只给能把它拉回来的人看：库里唯一的动作是「重新纳入财政审核」，
     权限在审核岗和管理员（flow.reserve_reactivate）。申报单位看到这一栏
     只会问「跟我的件有什么关系」；初审岗点进去也是没有一个能按的按钮。
     他自己的件进了储备库，在「已办项目」按阶段筛「项目储备库」仍看得到。 */
  if (userStore.isAdmin || userStore.role === 'auditor') {
    biz.push({ name: 'reserve', label: '项目储备库', hint: '暂无资金安排，可重新纳入' })
  }
  // 历史档案只给管理员：判重只需要系统算出的疑点，不需要把历年别家
  // 单位的可研翻一遍。开放给业务角色等于给了一条摸底旧项目的路
  if (userStore.isAdmin) {
    biz.push({ name: 'archive', label: '历史档案', hint: '判重比对' })
  }
  // 统计是给管理方看的：申报单位只看自己那几个项目，看统计只会看到一条线
  if (userStore.role !== 'applicant') {
    biz.push({ name: 'stats', label: '统计分析', hint: '耗时与导出' })
  }

  const sys = []
  if (userStore.isAdmin) {
    sys.push(
      { name: 'templates', label: '模板库', hint: '文书模板与环节绑定' },
      { name: 'users', label: '账号管理', hint: '新建与停用' },
      { name: 'settings', label: '系统设置', hint: '模型与规则' },
    )
  }

  const groups = [
    { label: '工作台', items: work },
    { label: '业务办理', items: biz },
  ]
  if (sys.length) groups.push({ label: '系统管理', items: sys })
  return groups
})

// 页头名字与侧栏、详情页返回按钮统一为「已办项目」
const pageTitle = computed(() => {
  if (route.name === 'projects') return '已办项目'
  if (route.name === 'reserve') return '项目储备库'
  if (route.name === 'project-detail') return '项目详情'
  return route.meta.title
})
const pageSub = computed(() => {
  if (route.name === 'projects' && userStore.role === 'applicant') {
    return '我报过的件，点开看进度'
  }
  if (route.name === 'reserve') {
    return '财政暂无资金安排的项目，有资金时重新纳入财政审核'
  }
  if (route.name === 'projects') {
    return '我经手过的件，只读回看'
  }
  return route.meta.sub
})

/* 导航图标：内联 SVG 片段，与模板同一套线性图标，避免再引一套图标库 */
const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="7" rx="1.6"/><rect x="14" y="3" width="7" height="7" rx="1.6"/><rect x="3" y="14" width="7" height="7" rx="1.6"/><rect x="14" y="14" width="7" height="7" rx="1.6"/>',
  projects: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.7l2 2.2h7.3A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5v-10Z"/>',
  reserve: '<path d="M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3H4V7Z"/><path d="M4 10h16v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7Z"/><path d="M9 14h6"/>',
  inbox: '<path d="M3.5 13h4l1.2 2.5h6.6L16.5 13h4"/><path d="M6 5.5h12l2.5 7v5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-5z"/>',
  archive: '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/><path d="M10 12h4"/>',
  stats: '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
  templates: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1.11 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34H9a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87V9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1Z"/>',
}

/* 详情页归位：从哪个箱子点进来，侧栏就停在哪个箱子上。
 * 原来详情页一律算「已办项目」，从待办点进来像被扔进了台账 ——
 * 件其实还在我手上，没办完，凭什么归到已办里。
 * from 由各入口自己带：待办 todo、在办箱 inbox、已办项目 projects；
 * 直接敲地址进来的（没有 from）仍按已办项目算，与老链接兼容。
 */
const DETAIL_ROUTES = ['project-detail', 'project-flow']
const FROM_BOX = { todo: 'dashboard', inbox: 'inbox', projects: 'projects',
  reserve: 'reserve' }
const fromBox = computed(() => {
  const f = String(route.query.from || '')
  return Object.prototype.hasOwnProperty.call(FROM_BOX, f) ? FROM_BOX[f] : 'projects'
})
const isActive = (name) => {
  if (!DETAIL_ROUTES.includes(route.name)) return route.name === name
  return name === fromBox.value
}

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确认退出登录？', '提示', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return // 取消退出
  }
  // 必须先清本地 token 再跳转：路由守卫看到 pm_access 还在，
  // 会把 /login 重定向回首页，出现「已退出但停在首页」的状态。
  await userStore.logout()
  router.push({ name: 'login' })
}

function onUserCmd(cmd) {
  if (cmd === 'pwd') openPwd()
  else if (cmd === 'logout') handleLogout()
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            <path fill="currentColor" d="M4 4h7v7H4V4zm9 0h7v7h-7V4zM4 13h7v7H4v-7zm9 0h7v7h-7v-7z" />
          </svg>
        </div>
        <div>
          <div class="brand-name">信息化项目管理</div>
          <div class="brand-sub">全生命周期 · 服务器版</div>
        </div>
      </div>

      <div class="nav-wrap">
        <div v-for="g in navGroups" :key="g.label" class="nav-group">
          <div class="nav-label">{{ g.label }}</div>
          <button
            v-for="n in g.items"
            :key="n.name"
            class="nav-item"
            :class="{ active: isActive(n.name) }"
            :title="n.hint"
            @click="router.push({ name: n.name })"
          >
            <svg class="icon" viewBox="0 0 24 24" v-html="ICONS[n.name]" />
            <span class="nav-text">{{ n.label }}</span>
            <span v-if="n.badge" class="nav-badge">{{ n.badge }}</span>
          </button>
        </div>
      </div>

      <div class="sidebar-foot">
        <div class="server-state">
          <span class="server-dot" />
          数据留于本机服务器
          <span class="build-tag">{{ buildAt }}</span>
        </div>
        <el-dropdown trigger="click" placement="top-end" popper-class="side-user-menu" @command="onUserCmd">
          <div class="user-card">
            <div class="user-avatar">{{ (userStore.display || '·').slice(0, 1) }}</div>
            <div class="user-text">
              <div class="user-name">{{ userStore.display }}</div>
              <div class="user-role">
                {{ userStore.user?.role_label || '—' }}
                <template v-if="userStore.user?.unit"> · {{ userStore.user.unit }}</template>
              </div>
            </div>
            <span class="user-more" aria-hidden="true">
              <svg class="icon" viewBox="0 0 24 24">
                <circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" />
              </svg>
            </span>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="pwd">修改密码</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="topbar-left">
          <div>
            <h1 class="top-title">{{ pageTitle }}</h1>
            <p class="top-sub">{{ pageSub }}</p>
          </div>
        </div>
        <div class="topbar-right">
          <button v-if="ui.pending" class="todo-pill" @click="router.push({ name: 'dashboard' })">
            <span class="dot" />
            待办 {{ ui.pending }}
          </button>
          <span class="env-tag">
            <span class="env-dot" />
            内网部署
          </span>
        </div>
      </header>
      <section ref="viewEl" class="view">
        <RouterView />
      </section>
    </main>

    <el-dialog
      v-model="pwdShow"
      :title="mustChange ? '首次登录，请修改初始密码' : '修改密码'"
      width="440px"
      :show-close="!mustChange"
      :close-on-click-modal="false"
      :close-on-press-escape="!mustChange"
    >
      <el-alert
        v-if="mustChange"
        type="warning"
        :closable="false"
        show-icon
        title="初始密码是部署文档里的公开信息，必须改掉后才能继续使用。"
        style="margin-bottom: 14px"
      />
      <el-form label-position="top">
        <el-form-item label="原密码" required>
          <el-input
            v-model="pwdForm.old_password"
            type="password"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>
        <el-form-item label="新密码（至少 8 位，不能是纯数字）" required>
          <el-input
            v-model="pwdForm.new_password"
            type="password"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item label="确认新密码" required>
          <el-input
            v-model="pwdForm.confirm"
            type="password"
            show-password
            autocomplete="new-password"
            @keyup.enter="submitPwd"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button v-if="!mustChange" @click="pwdShow = false">取消</el-button>
        <el-button type="primary" :loading="pwdSaving" @click="submitPwd">
          {{ mustChange ? '修改并继续' : '保存' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
}

/* ---------------------------------------------------------------- 侧栏 */
/* 深蓝渐变立住政务调性：底色压暗，靠一条 3px 高亮条与浅底提示选中项 */
.sidebar {
  position: relative;
  width: var(--sidebar-w);
  flex: 0 0 var(--sidebar-w);
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: #fff;
  background: linear-gradient(180deg, #0A1F38 0%, #0C2544 52%, #0A213C 100%);
  z-index: 40;
}
.sidebar::before {
  content: "";
  position: absolute;
  width: 340px;
  height: 340px;
  top: -170px;
  right: -150px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(47, 128, 237, 0.30), transparent 66%);
  pointer-events: none;
}
.sidebar::after {
  content: "";
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.024) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.024) 1px, transparent 1px);
  background-size: 32px 32px;
  -webkit-mask-image: linear-gradient(180deg, #000, transparent 78%);
  mask-image: linear-gradient(180deg, #000, transparent 78%);
  pointer-events: none;
}

.brand {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 22px 20px 18px;
}
.brand-mark {
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  border-radius: 11px;
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, #3B8BF2, #1D5FAE 55%, #154B8A);
  border: 1px solid rgba(255, 255, 255, 0.18);
  box-shadow: 0 10px 24px -10px rgba(47, 128, 237, 0.75);
  color: #fff;
}
.brand-name {
  font-size: 14.5px;
  font-weight: 600;
  letter-spacing: 0.3px;
  line-height: 1.3;
}
.brand-sub {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.46);
  letter-spacing: 0.08em;
  margin-top: 2px;
}

.nav-wrap {
  position: relative;
  z-index: 1;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 4px 12px 10px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.2) transparent;
}
.nav-wrap::-webkit-scrollbar { width: 10px; }
.nav-wrap::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.16);
  border-radius: 8px;
  border: 3px solid transparent;
  background-clip: content-box;
}

.nav-group { margin-top: 14px; }
.nav-group:first-child { margin-top: 2px; }
.nav-label {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.16em;
  color: rgba(255, 255, 255, 0.38);
  padding: 0 10px 7px;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  height: 40px;
  padding: 0 11px;
  margin: 1px 0;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: rgba(255, 255, 255, 0.70);
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.nav-item .icon {
  width: 17px;
  height: 17px;
  color: rgba(255, 255, 255, 0.50);
  transition: color 0.15s;
}
.nav-item:hover { background: rgba(255, 255, 255, 0.06); color: #fff; }
.nav-item:hover .icon { color: rgba(255, 255, 255, 0.82); }
.nav-item.active {
  background: linear-gradient(90deg, rgba(47, 128, 237, 0.30), rgba(47, 128, 237, 0.12));
  color: #fff;
  font-weight: 500;
}
.nav-item.active::before {
  content: "";
  position: absolute;
  left: -12px;
  top: 9px;
  bottom: 9px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: #5AA2F0;
}
.nav-item.active .icon { color: #8CC2FF; }
.nav-text { flex: 1; }

.nav-badge {
  margin-left: auto;
  min-width: 20px;
  height: 19px;
  padding: 0 6px;
  border-radius: 6px;
  background: #D92D20;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  display: grid;
  place-items: center;
  font-variant-numeric: tabular-nums;
}

/* ---------------------------------------------------------------- 侧栏底部 */
.sidebar-foot {
  position: relative;
  z-index: 1;
  padding: 12px 14px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
/* 下拉触发器默认 inline-flex，会缩成内容宽，撑不满侧栏 */
.sidebar-foot :deep(.el-dropdown) { display: block; width: 100%; }
.server-state {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 8px 10px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.44);
  letter-spacing: 0.02em;
}
/* 构建时间：很小、靠右，不抢「数据留于本机服务器」这行
   判断版本主要靠它 —— 旧页面没有这串数字 */
.build-tag {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: rgba(255, 255, 255, 0.3);
}
.server-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #3DD68C;
  box-shadow: 0 0 0 3px rgba(61, 214, 140, 0.14);
  flex: 0 0 6px;
}
.user-card {
  display: flex;
  align-items: center;
  gap: 10px;  padding: 9px 8px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.055);
  border: 1px solid rgba(255, 255, 255, 0.07);
  cursor: pointer;
  transition: background 0.15s;
}
.user-card:hover { background: rgba(255, 255, 255, 0.09); }
.user-avatar {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, #4D9AF0, #1D5FAE);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}
.user-text { min-width: 0; }
.user-name {
  font-size: 12.5px;
  font-weight: 500;
  color: #fff;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.user-role {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.46);
  margin-top: 1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 148px;
}
.user-more {
  margin-left: auto;
  width: 26px;
  height: 26px;
  flex: 0 0 26px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  color: rgba(255, 255, 255, 0.48);
}
.user-more .icon { width: 16px; height: 16px; fill: currentColor; stroke: none; }
.user-more:hover { background: rgba(255, 255, 255, 0.1); color: #fff; }

/* ---------------------------------------------------------------- 主区 */
.main {
  flex: 1;
  min-width: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.topbar {
  height: var(--topbar-h);
  flex: 0 0 var(--topbar-h);
  display: flex;
  align-items: center;
  gap: 16px;
  /* 与内容区左右留白对齐，标题和卡片左边线落在同一条竖线上 */
  padding: 0 44px;
  background: rgba(255, 255, 255, 0.9);
  -webkit-backdrop-filter: saturate(160%) blur(12px);
  backdrop-filter: saturate(160%) blur(12px);
  border-bottom: 1px solid var(--line);
  position: relative;
  z-index: 30;
}
.topbar-left { display: flex; align-items: center; gap: 12px; min-width: 0; }
.top-title {
  font-size: 16.5px;
  font-weight: 600;
  letter-spacing: -0.1px;
  line-height: 1.25;
  margin: 0;
  color: var(--ink);
  white-space: nowrap;
}
.top-sub {
  font-size: 11.5px;
  color: var(--ink-3);
  margin: 1px 0 0;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.topbar-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }

.todo-pill {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--primary-line);
  background: var(--primary-soft);
  color: var(--primary-deep);
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: 0.15s;
}
.todo-pill:hover { background: #DFECFB; border-color: #B9D3F0; }
.todo-pill .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--primary); }

.env-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ink-4);
  letter-spacing: 0.02em;
}
.env-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #12B76A;
  box-shadow: 0 0 0 3px rgba(18, 183, 106, 0.12);
}

/* 唯一滚动区：高度固定，内容超了只在这里滚 */
.view {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  /* 左右留白：卡片要浮在灰底上，不是压在框线上。
     28 → 44 仍被反馈「贴着边界」，定到 56 */
  padding: 28px 56px 60px;
  scroll-behavior: smooth;
}

@media (max-width: 1180px) {
  .sidebar { width: 216px; flex-basis: 216px; }
  .topbar { padding: 0 24px; }
  /* 窄屏也保证白卡与窗口有 40px 的断开，不能再收到 24 */
  .view { padding: 24px 40px 48px; }
}
</style>

<style>
/* 侧栏用户菜单走 teleport 到 body，scoped 样式够不到，单独给一段 */
.side-user-menu.el-dropdown__popper.el-popper {
  border-radius: 10px;
  border-color: var(--line);
  box-shadow: var(--shadow-pop);
}
</style>
