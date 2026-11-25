/**
 * 原件预览的公共状态：取 blob → 判类型 → 内联预览 / 本机程序打开 / 下载 → 回收 objectURL。
 *
 * 档案页与项目详情页都要「打开原件」，逻辑放一份，避免两处对 PDF/Word 的
 * 判断慢慢走偏（一处改了就内联、另一处还在下载，用户会以为是坏文件）。
 */
import { onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { blobErrorDetail, downloadBlob, isPreviewable, normalizeBlob } from '@/utils/fileview'

export function useRawFile() {
  const visible = ref(false)
  const url = ref('')
  const name = ref('')

  function revoke() {
    if (url.value) {
      URL.revokeObjectURL(url.value)
      url.value = ''
    }
  }

  function close() {
    visible.value = false
    revoke()
  }

  /**
   * 请后端把文件落到本机下载目录并调起系统默认程序。
   * 返回 true 表示已处理（打开成功，或已落盘但系统没自动开起来并已提示），
   * 返回 false 表示没接住，调用方继续走下载。
   */
  async function tryHostOpen(opener, filename) {
    try {
      const { data } = await opener()
      if (data?.opened) {
        ElMessage.success({
          message: `已用默认程序打开：${data.filename || filename}`,
          duration: 3000,
        })
        return true
      }
      if (data?.path) {
        ElMessage.warning(data.detail || `已保存到 ${data.path}，请手动双击打开`)
        return true
      }
      return false
    } catch (err) {
      const status = err?.response?.status
      const detail = err?.response?.data?.detail
      // 409 = 不是从本机访问，后端不会替远端电脑弹窗，直接下载
      if (status === 409) {
        ElMessage.info(detail || '当前不是本机访问，已改为下载')
        return false
      }
      ElMessage.warning(detail ? `${detail}，已改为下载` : '本机打开失败，已改为下载')
      return false
    }
  }

  /**
   * @param {() => Promise<{data: Blob}>} loader 取原件流的请求
   * @param {string} filename 材料原名，决定预览还是打开
   * @param {(() => Promise<any>)?} hostOpener 让后端调起本机程序的请求；
   *        不传则对不能预览的格式一律下载
   */
  async function open(loader, filename, hostOpener) {
    // 浏览器不认的格式先让本机程序试一次，省掉「下载→找文件→双击」三步。
    // 放前面是为了不必先把整份文件取成 blob 再丢掉。
    if (hostOpener && !isPreviewable(filename)) {
      if (await tryHostOpen(hostOpener, filename)) return
    }

    try {
      const { data } = await loader()
      if (!data || !data.size) {
        ElMessage.warning('原件为空或已被清理，请联系管理员核对')
        return
      }
      const blob = normalizeBlob(data, filename)
      if (isPreviewable(filename)) {
        revoke()
        url.value = URL.createObjectURL(blob)
        name.value = filename
        visible.value = true
      } else {
        downloadBlob(blob, filename)
        ElMessage.success(`已开始下载：${filename}`)
      }
    } catch (err) {
      ElMessage.error(await blobErrorDetail(err, '原件打开失败'))
    }
  }

  onBeforeUnmount(revoke)

  return { visible, url, name, open, close }
}
