import { defineStore } from 'pinia'
import { projectApi } from '@/api'

/**
 * 侧栏两个角标放在全局 store 里：待办（还没动手）与在办（存过草稿）。
 *
 * 原来数字是 Layout 的局部 ref，只有切路由时才重新拉一次；项目详情页
 * 标「已读」之后、存或删草稿之后，Layout 拿不到通知，数字要等下一次
 * 切页才变。提到 store 后，页面里直接调 refreshPending()，角标当场跟着变。
 */
export const useUiStore = defineStore('ui', {
  state: () => ({ pending: 0, doing: 0 }),

  actions: {
    async refreshPending() {
      try {
        const { data } = await projectApi.pending()
        this.pending = data.pending || 0
        this.doing = data.doing || 0
      } catch {
        /* 角标拉取失败不影响业务操作 */
      }
    },
  },
})
