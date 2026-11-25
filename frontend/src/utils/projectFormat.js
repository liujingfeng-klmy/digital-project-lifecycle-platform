/* 项目详情页共用的阶段顺序与显示格式（#26 从 ProjectDetail.vue 抽出）。
   纯数据与纯函数，不依赖组件实例，便于单测与复用。 */

export const ORDER = [
  { key: 'draft', label: '申报中' },
  { key: 'precheck', label: '预审中' },
  { key: 'supplement', label: '待补正' },
  { key: 'expert_review', label: '评审中' },
  { key: 'review_supplement', label: '评审后补正' },
  { key: 'cost_review', label: '费用评审' },
  { key: 'cost_supplement', label: '征求反馈' },
  { key: 'fiscal_review', label: '财政审核' },
  { key: 'approval', label: '立项' },
  { key: 'tendering', label: '待招标' },
  { key: 'building', label: '建设中' },
  { key: 'trial', label: '试运行' },
  { key: 'acceptance', label: '待验收' },
  { key: 'accepted', label: '已验收' },
]

export const STAGE_HINT = {
  draft: '填写信息、上传材料后提交预审。',
  precheck: '规则预审与 AI 审查后出初审意见。',
  supplement: '按补正单修改后重新提交。',
  expert_review: '录入票数与材料，生成评审结论。',
  review_supplement: '按评审意见修改可研，改完交费用评审。',
  cost_review: '生成任务书，收到报告后出结论。',
  cost_supplement: '等申报单位确认评估意见。',
  fiscal_review: '财政局审定资金安排。',
  approval: '出具立项批复后转待招标。',
  tendering: '招标由建设单位自行组织。',
  building: '建设过程由建设单位自行管理。',
  trial: '试运行由建设单位组织。',
  acceptance: '出具数据归集证明，验收后结项。',
  accepted: '',
  reserve: '暂无资金安排，项目存入储备库。',
  rejected: '未通过评审，流程终止。',
}

export const STAGE_LABEL = Object.fromEntries(ORDER.map((s) => [s.key, s.label]))

export function stageLabel(key) {
  return STAGE_LABEL[key] || { rejected: '不予通过', reserve: '项目储备库' }[key] || key
}

export function fmtTime(v) {
  return v ? String(v).replace('T', ' ').slice(0, 16) : '—'
}

export function fmtMoney(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export function sevClass(s) {
  return s === 'block' ? 'sv-block' : s === 'warn' ? 'sv-warn' : 'sv-pass'
}

export function sevLabel(s) {
  return s === 'block' ? '必须补正' : s === 'warn' ? '需说明' : '通过'
}

export function levelClass(l) {
  return l === 'attention' ? 'lv-attention' : l === 'verify' ? 'lv-verify' : 'lv-ok'
}
