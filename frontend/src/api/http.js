import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({
  baseURL: '/api',
  timeout: 180000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('pm_access')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

let redirecting = false
let refreshing = null

/** 把 DRF 的错误体展平成一条可读消息（含字段级错误）。 */
function flattenError(data) {
  if (!data || typeof data !== 'object') return null
  if (typeof data.detail === 'string') return data.detail
  if (typeof data.error === 'string') return data.error
  if (Array.isArray(data.non_field_errors) && data.non_field_errors.length) {
    return String(data.non_field_errors[0])
  }
  // 字段错误：{"username": ["已存在"], "password": ["太简单"]}
  // 原来只认上面三种，字段错误取不到 → 拦截器不弹，用户以为"点了没反应"
  const parts = []
  for (const [field, val] of Object.entries(data)) {
    if (Array.isArray(val)) parts.push(`${field}：${val.join('、')}`)
    else if (typeof val === 'string') parts.push(`${field}：${val}`)
  }
  return parts.length ? parts.join('；') : null
}

// 去重：拦截器弹过后，组件里若又用 ElMessage.error(detail) 弹一次，
// 1 秒内相同文案不再重复弹。既补上字段错误提示，又消除双红条。
let lastMessage = ''
let lastMessageAt = 0
export function showError(msg) {
  const now = Date.now()
  if (msg === lastMessage && now - lastMessageAt < 1000) return
  lastMessage = msg
  lastMessageAt = now
  ElMessage.error(msg)
}

/** 用 refresh token 换新的 access。失败返回 null，由调用方走登出。 */
async function refreshAccess() {
  const refresh = localStorage.getItem('pm_refresh')
  if (!refresh) return null
  // 用裸 axios，不能走本实例的拦截器，否则 401 会递归
  const { data } = await axios.post('/api/auth/refresh', { refresh })
  if (data.access) {
    localStorage.setItem('pm_access', data.access)
    if (data.refresh) localStorage.setItem('pm_refresh', data.refresh)
  }
  return data.access || null
}

function goLogin() {
  localStorage.removeItem('pm_access')
  localStorage.removeItem('pm_refresh')
  if (!redirecting && !location.hash.includes('/login')) {
    redirecting = true
    location.hash = '#/login'
    setTimeout(() => { redirecting = false }, 800)
    ElMessage.warning('登录已过期，请重新登录')
  }
}

http.interceptors.response.use(
  (res) => res,
  async (error) => {
    const status = error.response?.status
    const detail = flattenError(error.response?.data)

    // access 到期先静默续期再重放，别让用户填到一半被踢回登录页。
    // 401 来自 /auth/ 本身（登录、改密、refresh）时不重试，否则会打转。
    const cfg = error.config || {}
    const isAuthPath = typeof cfg.url === 'string' && cfg.url.includes('/auth/')
    if (status === 401 && !cfg.__retried && !isAuthPath) {
      try {
        refreshing = refreshing || refreshAccess()
        const token = await refreshing
        refreshing = null
        if (token) {
          cfg.__retried = true
          cfg.headers = cfg.headers || {}
          cfg.headers.Authorization = `Bearer ${token}`
          return http(cfg)
        }
      } catch {
        refreshing = null
      }
      goLogin()
    } else if (status === 401) {
      goLogin()
    } else if (detail && !cfg.silent) {
      // silent：预期内的「失败」（比如待办已读登记，项目本来就不是我的待办），
      // 调用方自己处理，不要弹红条吓人
      showError(detail)
    } else if (status >= 500) {
      showError('服务端错误，请查看后端日志')
    }
    return Promise.reject(error)
  },
)

export default http
