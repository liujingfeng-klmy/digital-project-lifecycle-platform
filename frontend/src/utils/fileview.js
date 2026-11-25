/**
 * 原件 blob 的打开方式。
 *
 * 为什么不在新窗口里直接打开接口地址：接口要 Bearer token，新窗口带不上，
 * 只能先取成 blob 再造本地链接。
 *
 * 分工：
 * - 浏览器自己能渲染的（PDF、图片、纯文本、CSV）走页内预览，不弹新标签；
 * - Word / Excel / 压缩包之类浏览器不认的，直接落成下载，否则只会得到
 *   一个空白标签页和一份没头没尾的临时文件。
 */

const PREVIEWABLE = new Set([
  'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg',
  'txt', 'md', 'csv', 'json', 'log',
])

const MIME = {
  pdf: 'application/pdf',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  bmp: 'image/bmp',
  svg: 'image/svg+xml',
  txt: 'text/plain;charset=utf-8',
  md: 'text/plain;charset=utf-8',
  csv: 'text/csv;charset=utf-8',
  json: 'application/json',
  log: 'text/plain;charset=utf-8',
}

export function extOf(name) {
  const i = String(name || '').lastIndexOf('.')
  return i > -1 ? name.slice(i + 1).toLowerCase() : ''
}

export function isPreviewable(name) {
  return PREVIEWABLE.has(extOf(name))
}

/** 后端没给 Content-Type（或给了 octet-stream）时，按扩展名补一个，否则 PDF 会被下载而不是预览。 */
export function normalizeBlob(blob, filename) {
  if (!blob) return blob
  if (blob.type && blob.type !== 'application/octet-stream') return blob
  return new Blob([blob], { type: MIME[extOf(filename)] || 'application/octet-stream' })
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || '原件'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10000)
}

/**
 * 下载类接口报错时，错误体也是 blob，拦截器读不出 detail。
 * 这里把它读回 JSON 再取提示语，免得用户只看到「服务端错误」。
 */
export async function blobErrorDetail(err, fallback = '文件打开失败') {
  const data = err?.response?.data
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text())
      if (parsed?.detail) return parsed.detail
    } catch { /* 不是 JSON，按状态码兜底 */ }
    return fallback
  }
  return data?.detail || err?.response?.statusText || fallback
}
