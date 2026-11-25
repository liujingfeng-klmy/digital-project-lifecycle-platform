/* 项目材料的下载动作：文书、评审会材料、老的合并扫描件。
 *
 * 三个页签都要能点开这些文件（项目资料、审核结论、本环节办理），原来都写在
 * 详情页脚本里。原件预览要挂一个弹层，只能有一处，所以留意外壳统一处理；
 * 纯下载的放这里，谁用谁引。
 */
import { ElMessage } from 'element-plus'
import { projectApi } from '@/api'
import { downloadBlob } from '@/utils/fileview'

export async function downloadDoc(projectId, doc) {
  if (!doc) return
  try {
    const { data } = await projectApi.documentFile(projectId, doc.id)
    downloadBlob(data, doc.name)
  } catch {
    ElMessage.error('文书下载失败，请稍后重试')
  }
}

export async function viewReviewFile(projectId, f) {
  try {
    const { data } = await projectApi.reviewFile(projectId, f.id)
    downloadBlob(data, f.name)
  } catch { /* 错误提示由拦截器统一弹出 */ }
}

export async function viewScan(projectId, reviewScan) {
  try {
    const { data } = await projectApi.reviewScanFile(projectId)
    downloadBlob(data, reviewScan?.name || '评审会材料.pdf')
  } catch { /* 错误提示由拦截器统一弹出 */ }
}
