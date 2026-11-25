/* 项目详情页的派生数据：页签徽标与页签内容共用同一份口径。
 *
 * 拆成纯函数放这里，是因为同一个数字要出现在两处 —— 页签上的徽标由外壳算，
 * 页签里的行由各自构件算。两处各写一份，迟早会一个说 6 条、一个显示 4 条。
 */
import { ORDER, stageLabel, fmtMoney } from '@/utils/projectFormat'

const STAGE_KEYS = ORDER.map((s) => s.key)
const stageAt = (k) => STAGE_KEYS.indexOf(k)

const TERMINAL_STAGES = ['accepted', 'rejected', 'reserve']

/* ------------------------------------------------------------ 流转记录
 * 一个环节一条记录。留痕是按动作逐条记的（预审里「规则预审」+「生成文书」
 * +「初审通过」就是三条），页签里照搬等于让人在二十几条里自己找环节。
 * 归并成一条之后，一条记录回答「这个环节办成了什么、谁办的、什么时候」。
 *
 * 例外：退回要求补充材料必须单列 —— 它是一次办理的起点，
 * 并进环节里就看不出「这一轮为什么重来」。退回之后同环节再办，会另起一条。
 */
export const RETURN_ACTS = ['precheck_return', 'cost_review_return', 'accept_return']

export function groupEvents(list) {
  const out = []
  let cur = null

  const build = (stage, items, extra = {}) => {
    const last = items[items.length - 1]
    // 决定这个环节结果的那一步：最后一次改变了阶段的那条，没有就用最后一条
    const decisive = [...items].reverse().find((x) => x.to_stage !== x.from_stage) || last
    const actors = [...new Set(items.map((x) => x.actor_name).filter(Boolean))]
    // 标题已经写了的那个动作，不在「几次操作」里再念一遍
    const acts = [...new Set(items.map((x) => x.action_label))]
      .filter((x) => x !== (extra.title || decisive.action_label))
    return {
      key: `${stage}-${items[0].id}`,
      stage,
      stageLabel: stageLabel(stage) || '申报',
      title: extra.title || decisive.action_label,
      note: decisive.comment || '',
      acts,
      actors: actors.join('、'),
      at: last.created,
      n: items.length,
      ret: false,
      ...extra,
    }
  }

  const flush = () => {
    if (!cur) return
    out.push(build(cur.stage, cur.items))
    cur = null
  }

  for (const e of list) {
    if (RETURN_ACTS.includes(e.action)) {
      /* 退回前的准备动作（生成任务书、上传材料）属于这一轮，
       * 单独留一组会变成「没有结论的一组」—— 并进这条退回记录，
       * 次数与小字都算在退回上，一轮只出现一次 */
      const pre = cur && cur.stage === e.from_stage ? cur.items : []
      cur = null
      out.push(build(e.from_stage, [...pre, e], {
        title: e.action_label,
        note: e.comment || '',
        at: e.created,
        ret: true,
        // 「退回补正」本身已经写明退回，不再多挂一个标签
        retTag: !String(e.action_label).includes('退回'),
      }))
      continue
    }
    const stage = e.from_stage || e.to_stage || ''
    if (!cur || cur.stage !== stage) {
      flush()
      cur = { stage, items: [] }
    }
    cur.items.push(e)
  }
  flush()
  return out
}

export const eventsAscOf = (p) => [...(p?.events || [])]
export const flowGroupsOf = (p) => groupEvents(eventsAscOf(p))


/* ------------------------------------------------------------ 材料选取
 * 同一条筛选要出现在两处：面板里列清单、上一级判断「缺这份能不能过」。
 * 写在两处迟早会一边按 kind 一边按 doc_type 过滤，各说各话。
 */
export const costTasksOf = (p) => (p?.documents || [])
  .filter((d) => d.kind === 'cost_review_task')
export const costTaskDocOf = (p) => costTasksOf(p)[0] || null
export const costReportsOf = (p) => (p?.files || [])
  .filter((f) => f.doc_type === 'cost_report')
export const collectDocOf = (p) => (p?.documents || [])
  .find((d) => d.kind === 'data_collect') || null
export const acceptOpinionsOf = (p) => (p?.files || [])
  .filter((f) => f.doc_type === 'accept_opinion')

/* 本轮报告回来之后就不该再挂「等报告」：件已经到了该出结论的时候，
 * 还挂着条保存条，等于让人以为报告没收到 */
export const costReportThisRoundOf = (p) => costReportsOf(p)
  .filter((f) => (f.round ?? 0) === (p?.round ?? 0))

/* 草稿保存时间：今天只显时:分，跨天带日期 */
export function fmtSaved(s) {
  if (!s) return ''
  const d = new Date(s)
  const now = new Date()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return d.toDateString() === now.toDateString()
    ? `今天 ${hm}` : `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')} ${hm}`
}

/* ------------------------------------------------------------ 项目资料
 * 评审会材料按类型归一条：五位专家就是五张表，一人一行会把清单拉长，
 * 而人要看的是「有没有专家意见」，不是先数有几张表。点开才列出各份文件名。
 */
export function reviewGroupsOf(p) {
  const map = new Map()
  for (const f of (p?.review_files || [])) {
    if (!map.has(f.kind)) map.set(f.kind, { kind: f.kind, label: f.kind_label, files: [] })
    map.get(f.kind).files.push(f)
  }
  return [...map.values()]
}

/* 项目资料按办理流程排：可研 → 评审会意见 → 评估报告 → 批复 → 归集证明。
 * 三段各按自己的顺序摞在一起（文件按 id、文书按生成时间倒序）会变成
 * 「单位报的一堆在前、系统文书一堆在后」，批复跑到归集证明前面。
 * 这里统一按「上传/生成时所在环节」取阶段序号，同环节再按时间先后。
 * 没标环节的老材料当作申报材料（排最前）。
 */
export function materialRowsOf(p) {
  const rows = []
  for (const f of (p?.files || [])) {
    rows.push({
      key: 'f' + f.id, kind: 'file', data: f,
      kindLabel: f.doc_type_label || '其他材料',
      stage: f.stage || 'draft', at: f.created || '',
    })
  }
  for (const d of (p?.documents || [])) {
    rows.push({
      key: 'd' + d.id, kind: 'doc', data: d,
      kindLabel: d.kind_label,
      stage: d.stage || 'draft', at: d.created || '',
    })
  }
  for (const g of reviewGroupsOf(p)) {
    // 评审会材料表上没有环节字段：评审会就是它的环节，组内取最早一份的时间
    const at = g.files.map((f) => f.created).filter(Boolean).sort()[0] || ''
    rows.push({
      key: 'rg' + g.kind, kind: 'review', group: g,
      kindLabel: '评审会材料', stage: 'expert_review', at,
    })
  }
  if (p?.review_scan) {
    rows.push({
      key: 'scan', kind: 'scan',
      kindLabel: '评审会材料', stage: 'expert_review', at: '',
    })
  }
  const rank = (s) => {
    const i = ORDER.findIndex((x) => x.key === s)
    return i < 0 ? 0 : i
  }
  // 按时间戳数值比，不按字符串：接口给的是带时区的 ISO，直接比较在跨时区会错位
  const ts = (v) => {
    const t = Date.parse(v)
    return Number.isFinite(t) ? t : Infinity
  }
  // 没时间的排在同环节末尾；同一时刻保留原始次序（文件→文书→评审会）
  return rows
    .map((r, i) => ({ ...r, _rank: rank(r.stage), _ts: ts(r.at), _i: i }))
    .sort((a, b) => a._rank - b._rank || a._ts - b._ts || a._i - b._i)
}

/* 数的是**行**：评审会材料归并成一条，徽标就不能按份数算 */
export const materialRowCountOf = (p) => materialRowsOf(p).length

/* ------------------------------------------------------------ 审核结论
 * 预审结果（规则 + AI）与重复建设对照属于预审/评审期的材料；
 * 评审会记录属于评审期与评审后补正；
 * 从「征求反馈」起，审批过程就与接手的人无关了（上游结论除外）。
 */
const PRE_REVIEW_STAGES = ['precheck', 'supplement', 'expert_review']
const REVIEW_STAGES = ['expert_review', 'review_supplement']
const UPSTREAM_CUTOFF = 'cost_supplement'

const REVIEW_SEEN = ['expert_review', 'review_supplement', 'cost_review',
  'cost_supplement', 'fiscal_review', 'approval', 'tendering', 'building',
  'trial', 'acceptance', 'accepted', 'reserve', 'rejected']

/* 结论不再合成「评审、费用与财政结论」一大块。
 * 每个环节只列它需要核对的上游结论，一行一条，后面的环节不提前剧透。
 * 终止、储备、已验收、或打开完整档案时，才把已有结论全部摊开。
 */
const CONCLUSION_DEFS = [
  {
    key: 'review', label: '评审结论', from: 'cost_review',
    value: (p) => p?.review_result_label,
    note: (p) => p?.review_note,
    at: (p) => p?.concluded_at,
  },
  {
    // 费用评审会退回来重评，所以从费用评审环节起就要能看到上一轮的结论；
    // 退回时 audit_result 不落值（只有通过/不通过才落），不会提前剧透本轮结论
    key: 'cost', label: '费用评审结论', from: 'cost_review',
    value: (p) => p?.audit_result_label,
    note: (p) => p?.audit_note,
    at: (p) => p?.audited_at,
  },
  {
    key: 'fiscal', label: '财政审核结论', from: 'approval',
    value: (p) => p?.fiscal_result_label,
    note: (p) => p?.fiscal_note,
    at: (p) => p?.fiscal_at,
  },
  {
    // 批复不再收文号，这一行改显示核定投资 —— 下游最想知道的就是批了多少钱
    key: 'approval', label: '立项批复', from: 'tendering',
    value: (p) => {
      const v = p?.budget_amount
      if (v !== null && v !== undefined) return `核定总投资 ${fmtMoney(v)} 万元`
      return p?.approval_at ? '批复已出具' : ''
    },
    note: (p) => p?.approval_note,
    at: (p) => p?.approval_at,
  },
]

/* 每个环节只留它真正要核对的那一条上游结论。这一页签的用处就在这儿：
 * 它回答「我这一步凭什么做判断」，不是把走过的每一步回放一遍 ——
 * 回放是「流转记录」的事。所以映射逐环节显式写明，没写到的环节就是
 * 没有结论要核对，页签自然不出现。待验收留空：验收看的是《数据归集证明》
 * 和试运行情况，批了多少钱不影响验收判断。 */
const UPSTREAM_KEEP = {
  cost_supplement: [],
  fiscal_review: ['cost'],
  approval: ['fiscal'],
  tendering: ['approval'],
  building: ['approval'],
  trial: ['approval'],
  acceptance: [],
}

/**
 * 审核结论页签的全部派生数据。
 * @param {object} p 项目
 * @param {{fullArchive?: boolean}} opts 完整档案开关
 */
export function conclusionModel(p, opts = {}) {
  const fullArchive = !!opts.fullArchive
  const stage = p?.stage
  const archived = stage === 'accepted'
  const upstreamHidden = !!p && !fullArchive
    && stageAt(stage) >= stageAt(UPSTREAM_CUTOFF)

  const votes = p?.votes
    || { agree: 0, modified: 0, reject: 0, total: 0, yes: 0, recorded: false }
  const reviewStageSeen = !!p && (REVIEW_SEEN.includes(stage) || votes.recorded)
  const voteSummary = votes.recorded
    ? `通过 ${votes.agree} · 修改后通过 ${votes.modified} · 不通过 ${votes.reject}`
      + `，通过 ${votes.yes}/${votes.total}`
    : ''

  const costRounds = p?.cost_flow || []
  const costReturnCount = costRounds.filter((r) => r.action === 'cost_review_return').length
  const showCostRounds = costRounds.length > 0
    && !upstreamHidden
    && (fullArchive || costReturnCount > 0
      || ['cost_review', 'cost_supplement', 'fiscal_review', 'approval',
        'tendering', 'building', 'trial', 'acceptance', 'accepted'].includes(stage))

  const notices = p?.notices || []
  const pastNotices = notices.filter((n) => n.status !== 'open')
  /* 已办结的历史轮次只在「这一步跟前几轮改动有关」时进审核结论。
   * 费用评审会退回重评，上一轮改了什么必须看得见；可到了财政、立项、招标、
   * 验收，前几轮的补正与接手的人无关 —— 摊在那里，人第一眼会以为这件又被打回了。
   * 要看过程去「流转记录」，退回那一条是单独成行的。 */
  const conclNotices = (fullArchive || ['rejected', 'reserve'].includes(stage))
    ? pastNotices
    : (upstreamHidden ? [] : pastNotices)

  const showPrecheckCards = fullArchive || PRE_REVIEW_STAGES.includes(stage)
  const showDupCards = showPrecheckCards
  const showReviewMeeting = !upstreamHidden && (fullArchive || REVIEW_STAGES.includes(stage))

  const showAll = fullArchive || TERMINAL_STAGES.includes(stage)
  const cur = stageAt(stage)
  const keep = upstreamHidden ? UPSTREAM_KEEP[stage] : null
  const conclusionRows = stage
    ? CONCLUSION_DEFS
      .filter((d) => showAll || (cur >= 0 && cur >= stageAt(d.from)))
      .filter((d) => (keep ? keep.includes(d.key) : true))
      .map((d) => ({ ...d, val: d.value(p), nt: d.note(p), at: d.at(p) }))
      .filter((d) => d.val)
    : []

  const conclusionTitle = TERMINAL_STAGES.includes(stage) ? '流程结论'
    : upstreamHidden ? '本环节需核对的结论'
      : ['cost_review', 'cost_supplement', 'fiscal_review'].includes(stage) ? '前置结论'
        : '环节结论汇总'

  /* 数出来的必须和页签里真能看见的一致：把看不见的报告、票数、轮次也算进去，
   * 页签标着 6、点开只剩一张补正记录，数字和内容对不上比没有数字更糟。 */
  let conclCount = 0
  if (!archived) {
    if (showCostRounds) conclCount += costRounds.length
    conclCount += conclNotices.length
    if (showReviewMeeting && reviewStageSeen) conclCount += 1
    conclCount += conclusionRows.length
    if (showPrecheckCards) conclCount += (p?.reports || []).length
  }

  return {
    archived,
    upstreamHidden,
    votes,
    voteSummary,
    reviewStageSeen,
    costRounds,
    costReturnCount,
    showCostRounds,
    conclNotices,
    showPrecheckCards,
    showDupCards,
    showReviewMeeting,
    conclusionRows,
    conclusionTitle,
    conclCount,
  }
}

/* ------------------------------------------------------------ 阶段进度
 * 终止态停在哪一步：用最后一条流转记录的 from_stage。储备库是「财政无资金」
 * 退出来的，不予通过可能是初审或评审会否掉的，只有留痕知道停在哪。
 */
export function stopStageKeyOf(p) {
  const s = p?.stage
  if (s !== 'reserve' && s !== 'rejected') return ''
  const evs = p?.events || []
  const last = evs[evs.length - 1]
  return last?.from_stage || (s === 'reserve' ? 'fiscal_review' : '')
}

export function stageIndexOf(p) {
  const i = ORDER.findIndex((s) => s.key === p?.stage)
  return i < 0 ? -1 : i
}

/* 每个步骤的状态：done 已过、now 在办、stop 停在这里。
 * 原来终止件一律 index=-1，整条进度全是白的 —— 办的十几步像没发生过。
 */
export function stepStatesOf(p) {
  const cur = stageIndexOf(p)
  const stopIdx = stopStageKeyOf(p)
    ? ORDER.findIndex((x) => x.key === stopStageKeyOf(p)) : -1
  return ORDER.map((x, i) => {
    if (cur >= 0) return i < cur ? 'done' : (i === cur ? 'now' : '')
    if (stopIdx >= 0) {
      if (i < stopIdx) return 'done'
      if (i === stopIdx) return 'stop'
    }
    return ''
  })
}

export function progressPctOf(p) {
  if (!p) return 0
  const at = stageIndexOf(p)
  const stop = stopStageKeyOf(p) ? ORDER.findIndex((x) => x.key === stopStageKeyOf(p)) : -1
  const i = at >= 0 ? at : (stop >= 0 ? stop : 0)
  return Math.round((i / (ORDER.length - 1)) * 100)
}
