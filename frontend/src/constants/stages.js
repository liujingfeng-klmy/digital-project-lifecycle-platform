/**
 * 阶段样式映射：阶段 code → 语义色类（draft/run/back/expert/audit/wait/done/stop）。
 *
 * 原来 Dashboard / Inbox / Projects / Materials 四个视图各写一份逐字相同的表，
 * 新增或调整阶段要改四处，极易漂移。这里作为唯一来源，四个视图统一引用。
 */
export const STAGE_STYLE = {
  draft: 'draft', precheck: 'run', supplement: 'back', expert_review: 'expert',
  review_supplement: 'back', cost_review: 'audit',
  cost_supplement: 'back', fiscal_review: 'audit',
  approval: 'audit', tendering: 'wait', building: 'run', trial: 'run',
  acceptance: 'audit', accepted: 'done', reserve: 'stop', rejected: 'stop',
}

export function stageStyle(code) {
  return STAGE_STYLE[code] || 'wait'
}
