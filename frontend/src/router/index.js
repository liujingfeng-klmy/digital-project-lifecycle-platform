import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/views/Layout.vue'),
    children: [
      {
        path: '',
        name: 'dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '待办', sub: '今天该你动手的件' },
      },
      {
        // 在办箱单独成页：填到一半的件停在这里，不跟待办混在一个页面上。
        // 待办页只回答「今天该我动手的有几件」
        path: 'inbox',
        name: 'inbox',
        component: () => import('@/views/Inbox.vue'),
        meta: { title: '在办箱', sub: '点开过、还没办完的件' },
      },
      {
        path: 'projects',
        name: 'projects',
        component: () => import('@/views/Projects.vue'),
        meta: { title: '已办项目', sub: '我经手过的项目，点开回看' },
      },
      {
        // 储备库单列一条：财政无资金安排的项目退到这里等资金，它不是终态，
        // 也不是谁的待办 —— 等有钱了由审核岗「重新纳入财政审核」接着走。
        // 藏在已办项目的阶段筛选里，需要时根本想不起来去哪儿找。
        // 只对能「重新纳入」的岗位开放（审核岗、管理员）：其他人进来
        // 没有一个能按的按钮，申报单位更用不上
        path: 'reserve',
        name: 'reserve',
        component: () => import('@/views/Projects.vue'),
        meta: { title: '项目储备库', sub: '暂无资金安排，有资金时重新纳入',
                roles: ['auditor', 'admin'] },
      },
      {
        path: 'stats',
        name: 'stats',
        component: () => import('@/views/Stats.vue'),
        meta: { title: '统计与归档', sub: '阶段耗时、通过率与一键导出' },
      },
      {
        path: 'projects/:id',
        name: 'project-detail',
        component: () => import('@/views/ProjectDetail.vue'),
        meta: { title: '项目详情', sub: '本环节办理、材料与结论' },
      },
      {
        // 流转记录单独成页：项目详情页只留办理需要的卡片，
        // 想看时间线就点进来，不再把两屏时间线堆在办理页底部
        path: 'projects/:id/flow',
        name: 'project-flow',
        component: () => import('@/views/ProjectFlow.vue'),
        meta: { title: '流转记录', sub: '全部动作、阶段流转与经办意见' },
      },
      {
        path: 'archive',
        name: 'archive',
        component: () => import('@/views/Archive.vue'),
        // 历史档案只给管理员：里面是历年别家单位的可研与批复，
        // 判重用的是系统算出的疑点，不需要放开原档。菜单不显示，
        // 直接敲地址也被这里挡住，后端接口同样只认管理员
        meta: { title: '历史档案', sub: '过往项目材料入库与重复建设比对', admin: true },
      },
      {
        path: 'templates',
        name: 'templates',
        component: () => import('@/views/Templates.vue'),
        // 模板库只给管理员：业务角色不需要看到内部底稿
        meta: { title: '模板库', sub: '文书模板维护与环节绑定', admin: true },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/views/Settings.vue'),
        meta: { title: '系统设置', sub: '模型接入与流程规则', admin: true },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/Users.vue'),
        meta: { title: '账号管理', sub: '新建账号与角色分配', admin: true },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to) => {
  const token = localStorage.getItem('pm_access')
  if (!to.meta.public && !token) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && token) {
    return { name: 'dashboard' }
  }
  if (to.meta.admin) {
    const user = JSON.parse(localStorage.getItem('pm_user') || 'null')
    if (user?.role !== 'admin') {
      return { name: 'dashboard' }
    }
  }
  if (to.meta.roles) {
    const user = JSON.parse(localStorage.getItem('pm_user') || 'null')
    if (user && !to.meta.roles.includes(user.role)) {
      return { name: 'dashboard' }
    }
  }
  return true
})

export default router
