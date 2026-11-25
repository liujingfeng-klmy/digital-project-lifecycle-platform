import http from './http'

export const authApi = {
  login: (data) => http.post('/auth/login', data),
  me: () => http.get('/auth/me'),
  changePassword: (data) => http.post('/auth/change-password', data),
  logout: (refresh) => http.post('/auth/logout', { refresh }, { silent: true }),
}

export const llmApi = {
  presets: () => http.get('/llm/presets'),
  config: () => http.get('/llm/config'),
  save: (data) => http.put('/llm/config', data),
  test: (data) => http.post('/llm/test', data || {}),
}

export const userApi = {
  list: (params) => http.get('/users/', { params }),
  create: (data) => http.post('/users/', data),
  update: (id, data) => http.patch(`/users/${id}/`, data),
  remove: (id) => http.delete(`/users/${id}/`),
  // 重置口令：连带把对方已登录的会话踢掉，必须走独立接口而不是 PATCH
  resetPassword: (id, data) => http.post(`/users/${id}/reset-password/`, data),
}

// 角色与账号分开维护：角色在这里增删改，建号时选角色
export const roleApi = {
  list: (params) => http.get('/roles/', { params: { page_size: 200, ...params } }),
  create: (data) => http.post('/roles/', data),
  update: (id, data) => http.patch(`/roles/${id}/`, data),
  remove: (id) => http.delete(`/roles/${id}/`),
}

// 市本级申报单位名录。申报单与账号管理共用一套取值，不再自由输入
export const orgApi = {
  units: (params) => http.get('/org-units/', { params }),
}

// 环节审查时限：全员可读（办理人要看到还剩几天），仅管理员可改
export const slaApi = {
  list: () => http.get('/flow-sla/'),
  save: (items) => http.put('/flow-sla/', { items }),
}

export const archiveApi = {  stats: () => http.get('/archive/stats/'),
  list: (params) => http.get('/archive/projects/', { params }),
  detail: (id) => http.get(`/archive/projects/${id}/`),
  create: (data) => http.post('/archive/projects/', data),
  update: (id, data) => http.patch(`/archive/projects/${id}/`, data),
  remove: (id) => http.delete(`/archive/projects/${id}/`),
  merge: (data) => http.post('/archive/projects/merge/', data),
  rebuild: () => http.post('/archive/projects/rebuild/', {}),
  // 年度核对：识别规则改过之后，先列出差异再让人勾选，不自动覆盖
  yearDiff: () => http.get('/archive/projects/year-diff/'),
  yearApply: (ids) => http.post('/archive/projects/year-apply/', { ids }),
  importFiles: (formData, onProgress) =>
    http.post('/archive/projects/import/', formData, {
      timeout: 15 * 60 * 1000,
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    }),
  file: (id) => http.get(`/archive/files/${id}/`),
  fileText: (id) => http.get(`/archive/files/${id}/text/`),
  // 原件流：页面里取成 blob 再打开，避免把带鉴权的地址直接扔给新窗口
  raw: (id) => http.get(`/archive/files/${id}/raw/`, { responseType: 'blob' }),
  // 交给本机默认程序打开（Word/Excel 这类浏览器不认的格式）。仅本机访问时可用
  openLocal: (id) => http.post(`/archive/files/${id}/open/`),
  removeFile: (id) => http.delete(`/archive/files/${id}/`),
  reparse: (id) => http.post(`/archive/files/${id}/reparse/`, {}),
  moveFile: (id, project) => http.post(`/archive/files/${id}/move/`, { project }),
  compare: (data, onProgress) =>
    http.post('/archive/compare/', data, {
      timeout: 10 * 60 * 1000,
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    }),
  reviews: (params) => http.get('/archive/reviews/', { params }),
  reviewCompare: (data) => http.post('/archive/reviews/', data),
}

// 模板库：仅管理员可访问，模板可挂到指定环节
export const templateApi = {
  list: () => http.get('/templates/', { params: { page_size: 200 } }),
  create: (formData) => http.post('/templates/', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  update: (id, formData) => http.patch(`/templates/${id}/`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }),
  remove: (id) => http.delete(`/templates/${id}/`),
  file: (id) => http.get(`/templates/${id}/file/`, { responseType: 'blob' }),
}

export const projectApi = {
  list: (params) => http.get('/projects/', { params }),
  // 我的两个箱子：待办箱（还没动手）+ 在办箱（存过草稿、没提交）
  inbox: () => http.get('/projects/inbox/'),
  // 办理草稿：填到一半先存下来，件留在在办箱
  draft: (id) => http.get(`/projects/${id}/draft/`),
  saveDraft: (id, data) => http.post(`/projects/${id}/draft/`, data),
  dropDraft: (id) => http.delete(`/projects/${id}/draft/`),
  detail: (id) => http.get(`/projects/${id}/`),
  create: (data) => http.post('/projects/', data),
  update: (id, data) => http.patch(`/projects/${id}/`, data),
  remove: (id) => http.delete(`/projects/${id}/`),
  // 先传材料后建项目：只解析、不落库，返回字段与暂存 token
  prefill: (formData, onProgress) =>
    http.post('/projects/prefill/', formData, {
      timeout: 10 * 60 * 1000,
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    }),
  // 可研报告模板：解析不全时让申报单位照模板改材料
  template: () => http.get('/projects/feasibility-template/', { responseType: 'blob' }),
  // AI 审查是同步跑完再返回，长材料要 1-3 分钟，超时不能按普通接口算
  flow: (id, data) => http.post(`/projects/${id}/flow/`, data, { timeout: 10 * 60 * 1000 }),
  // 管理员改派承办人：handler_id 为空表示收回岗位池
  reassign: (id, handlerId) => http.post(`/projects/${id}/reassign/`, { handler_id: handlerId }),
  upload: (id, formData, onProgress) =>
    http.post(`/projects/${id}/files/`, formData, {
      timeout: 10 * 60 * 1000,
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    }),
  removeFile: (id, fileId) => http.delete(`/projects/${id}/files/${fileId}/`),
  fileText: (id, fileId) => http.get(`/projects/${id}/files/${fileId}/text/`),
  fileRaw: (id, fileId) =>
    http.get(`/projects/${id}/files/${fileId}/raw/`, { responseType: 'blob' }),
  fileOpen: (id, fileId) => http.post(`/projects/${id}/files/${fileId}/open/`),
  fileType: (id, fileId, docType) =>
    http.patch(`/projects/${id}/files/${fileId}/type/`, { doc_type: docType }),
  // 补正前后对照：默认比同类型上一版，可传 against 指定版本
  fileDiff: (id, fileId, against) =>
    http.get(`/projects/${id}/files/${fileId}/diff/`, {
      params: against ? { against } : {},
      timeout: 120000,
    }),
  // 评审会材料：各专家意见表、汇总意见表，一份一条记录，可一次传多份
  reviewFilesUpload: (id, formData) =>
    http.post(`/projects/${id}/review-files/`, formData, {
      timeout: 5 * 60 * 1000,
      onUploadProgress: () => {},
    }),
  reviewFileDelete: (id, fileId) =>
    http.delete(`/projects/${id}/review-files/${fileId}/`),
  reviewFile: (id, fileId) =>
    http.get(`/projects/${id}/review-files/${fileId}/file/`, { responseType: 'blob' }),
  // 老项目只有单份扫描件时的兼容入口
  reviewScanFile: (id) =>
    http.get(`/projects/${id}/review-scan/file/`, { responseType: 'blob' }),
  stats: (params) => http.get('/projects/stats/', { params }),
  stages: (params) => http.get('/projects/stages/', { params }),
  todos: () => http.get('/projects/todos/'),
  pending: () => http.get('/projects/pending/'),
  // 重新跑一次重复建设比对，生成逐项对照（含原文片段）
  dupRefresh: (id) => http.post(`/projects/${id}/dup-refresh/`, {}, { timeout: 5 * 60 * 1000 }),
  // 生成文书：预审修正说明 / 费用评审任务书 / 立项批复
  genPrecheckNotice: (id, data) => http.post(`/projects/${id}/docs/precheck-notice/`, data),
  genCostTask: (id, data) => http.post(`/projects/${id}/docs/cost-review-task/`, data),
  genApproval: (id, data) => http.post(`/projects/${id}/docs/approval-reply/`, data),
  // 待验收出具《数据归集证明》：按模板生成，不是上传扫描件
  genDataCollect: (id, data) => http.post(`/projects/${id}/docs/data-collect/`, data),
  documentFile: (id, docId) =>
    http.get(`/projects/${id}/documents/${docId}/file/`, { responseType: 'blob' }),
  // 结项归档：一个项目一份完整资料（材料含历史版本 + 生成文书 + 评审会材料 + 留痕）
  archive: (id) =>
    http.get(`/projects/${id}/archive/`, { responseType: 'blob', timeout: 5 * 60 * 1000 }),
  // 进过详情就记一条已读，侧栏待办角标减一
  todoAck: (id) => http.post(`/projects/${id}/todo-ack/`, null, { silent: true }),
  report: (params) => http.get('/projects/report/', { params }),
  filters: () => http.get('/projects/filters/'),
  exportCsv: (params) =>
    http.get('/projects/export/', { params, responseType: 'blob', timeout: 60000 }),
}
