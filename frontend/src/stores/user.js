import { defineStore } from 'pinia'
import { authApi } from '@/api'

export const useUserStore = defineStore('user', {
  state: () => ({
    access: localStorage.getItem('pm_access') || '',
    user: JSON.parse(localStorage.getItem('pm_user') || 'null'),
  }),

  getters: {
    isLoggedIn: (s) => !!s.access,
    role: (s) => s.user?.role || '',
    isAdmin: (s) => s.user?.role === 'admin',
    display: (s) => s.user?.display || s.user?.username || '',
  },

  actions: {
    async login(username, password) {
      const { data } = await authApi.login({ username, password })
      this.access = data.access
      this.user = data.user
      localStorage.setItem('pm_access', data.access)
      localStorage.setItem('pm_refresh', data.refresh)
      localStorage.setItem('pm_user', JSON.stringify(data.user))
      return data.user
    },

    async fetchMe() {
      const { data } = await authApi.me()
      this.user = data
      localStorage.setItem('pm_user', JSON.stringify(data))
      return data
    },

    /**
     * 改密。后端会把 token 版本 +1，旧 token 立即失效，
     * 同时下发一套新 token —— 这里直接替换，用户不用被踢出去重登。
     */
    async changePassword(payload) {
      const { data } = await authApi.changePassword(payload)
      if (data.access) {
        this.access = data.access
        this.user = data.user
        localStorage.setItem('pm_access', data.access)
        localStorage.setItem('pm_refresh', data.refresh)
        localStorage.setItem('pm_user', JSON.stringify(data.user))
      }
      return data
    },

    async logout() {
      // 先把 refresh token 交给后端加入黑名单，使已拷贝的 token 也无法续期；
      // 网络异常也不能卡住本地退出，所以失败照常清本地状态。
      const refresh = localStorage.getItem('pm_refresh')
      if (refresh) {
        try { await authApi.logout(refresh) } catch { /* 已失效或离线，忽略 */ }
      }
      this.access = ''
      this.user = null
      localStorage.removeItem('pm_access')
      localStorage.removeItem('pm_refresh')
      localStorage.removeItem('pm_user')
    },
  },
})
