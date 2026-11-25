"""流转状态机。

一个申报项目只能沿下面这条线走，每一步都有明确的责任角色：

    申报中 ──提交──> 预审中 ──初审通过──> 评审中 ──通过──> 费用评审
      ↑                │  ↑                 │  └─修改后通过─> 评审后补正 ─改完─> 费用评审
      │            退回补正│                 │  └─不通过──────────────────> 不予通过
      │                ↓  │                 │
      └──撤回──────── 待补正 ──重新提交──> 预审中
                                                          │
             费用评审通过 ──> 财政审核 ──同意──> 立项 ──批复出具──> 待招标
                                 │
                                 └──无资金安排──> 项目储备库

                    已验收 <──验收确认── 待验收 <──申请验收── 试运行 <──试运行── 建设中

设计上的几条硬约束（都写成了代码，不是提示语）：
1. 阶段不能被直接改，只能通过 `transition` 走动作；
2. 一级规则预审没跑过，不能出「初审通过」；
3. 一级预审有「必须补正」项，只能退回补正，不能带病上会；
4. 评审会结论必须凭签字意见表/汇总表录入，一张都没传不能出结论；
5. 票数合计须为单数，「通过 + 修改后通过」过半才算通过；
6. 结论为「修改后通过」的先转申报单位改材料，改完直接交费用评审，不回预审重来；
   结论为「不通过」的直接终止。
7. 费用评审必须先出《费用评审任务书》，立项必须先出《立项批复》——
   这两份对外文书是这一步的交付物，没出文书就不能往下走；
   费用评审的结论由第三方《费用评估报告》承担，没有报告不能记「通过」；
8. 财政审核不同意（无资金安排）的进项目储备库，不硬推招标，也不判死；
9. 待招标只确认「招标已完成」，不收中标通知书与合同 —— 那是建设单位与
   招标代理之间的民事文件；建设过程档案同样不收；
10. 试运行由建设单位执行：建设完成转试运行后，由申报单位填报试运行情况与
   「归集了哪些数据」，一并提出验收申请 —— 这两段文字就是归集证明的正文来源；
11. 待验收由我局出具《数据归集证明》（按模板生成，不是让单位传扫描件）：
   没有这份证明不能验收；专家验收意见在验收后由我局收存备案，
   验收确认那一刻不以它为前提。
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.core.models import User

from .models import (ActionDraft, PrecheckReport, Project, ProjectEvent,
                     ProjectFile, SupplementNotice)

APPLICANT = User.Role.APPLICANT
REVIEWER = User.Role.REVIEWER
AUDITOR = User.Role.AUDITOR
ADMIN = User.Role.ADMIN

# 按本地《数字化项目管理办法》，进入审批环节的项目都由市本级财政预算
# 安排资金，所以这一步不再让人挑资金来源，统一记这个值
FISCAL_FUND_SOURCE = '市本级财政预算'

STAGE_ROLE = {
    Project.Stage.DRAFT: APPLICANT,
    Project.Stage.PRECHECK: REVIEWER,
    Project.Stage.SUPPLEMENT: APPLICANT,
    # 评审会要求修改材料：改的是申报单位，改完由初审岗确认转费用评审
    Project.Stage.REVIEW_SUPPLEMENT: APPLICANT,
    # 评审会不再设专家账号：材料照旧转「评审中」，由初审岗办会、
    # 会后录入票数，所以本阶段的办理角色就是初审岗
    Project.Stage.EXPERT_REVIEW: REVIEWER,
    # 费用评审：审核岗把可研交财评机构、登记评估意见
    Project.Stage.COST_REVIEW: AUDITOR,
    # 征求反馈：把评估意见发单位，请其确认有无异议、是否补依据，回费用评审重评（第 N 轮）
    Project.Stage.COST_SUPPLEMENT: APPLICANT,
    # 财政审核：审核岗对接财政局，登记资金安排结论
    Project.Stage.FISCAL_REVIEW: AUDITOR,
    # 立项：初审岗按批复模板出具立项批复，发申报单位
    Project.Stage.APPROVAL: REVIEWER,
    Project.Stage.TENDERING: REVIEWER,
    Project.Stage.BUILDING: REVIEWER,
    # 试运行是建设单位在跑，情况与归集说明由他们填 —— 局方只提出具证明
    Project.Stage.TRIAL: APPLICANT,
    Project.Stage.ACCEPTANCE: AUDITOR,
    # 储备库是池子，不是某一个人的待办
    Project.Stage.RESERVE: '',
    Project.Stage.ACCEPTED: '',
    Project.Stage.REJECTED: '',
}

ROLE_LABEL = dict(User.Role.choices)

# 终态与池子：没有「下一个承办人」可言
TERMINAL_STAGES = {Project.Stage.RESERVE, Project.Stage.ACCEPTED,
                   Project.Stage.REJECTED}


# 角色代码 → 名称的进程内缓存。角色表很小、改动极少，而台账每行都要显示
# 承办岗位；不缓存的话列表页每行都会打一次角色表。
_ROLE_NAME_CACHE = {}


def clear_role_cache():
    _ROLE_NAME_CACHE.clear()


def role_name(code):
    """角色代码 → 显示名。先按角色表查，查不到再按权限域兜底。

    留痕里存的是代码，管理员把角色改名之后历史记录仍要显示得出来；
    角色被删（非内置才可删）时退回四类域的名称，不能显示成一串英文。
    """
    from apps.core.models import Role

    if not code:
        return ''
    if code in _ROLE_NAME_CACHE:
        return _ROLE_NAME_CACHE[code]
    name = (Role.objects.filter(code=code).values_list('name', flat=True).first()
            or ROLE_LABEL.get(code, code))
    _ROLE_NAME_CACHE[code] = name
    return name


def handler_role_of(stage):
    """该环节的办理岗位。

    管理员在「系统设置」里给某个环节指定了具体岗位（如把立项指给
    「立项审批岗」）就认那个，否则按四类权限域兜底。返回的是角色代码，
    显示时由 role_name() 翻成岗位名，历史留痕也跟着改名走。
    """
    return _stage_role_override(stage) or STAGE_ROLE.get(stage, '')


def _stage_role_override(stage):
    """环节级岗位覆盖。查库但只取一个字段，调用点都在单次请求内。"""
    from .models import StageSla

    rec = StageSla.objects.filter(stage=stage).only('role').first()
    return ((rec.role or '') if rec else '')


class FlowError(Exception):
    """流转被拒。消息直接给用户看，必须写清「为什么不能做、该怎么做」。"""


# ------------------------------------------------------------------ 动作定义
# form 里的字段由前端按 type 渲染，后端只认 key 与必填性 —— 前后端各写一套字段
# 容易漏，比如前端加了「评分」后端没接，用户填了白填。
def _f(key, label, type_='textarea', required=False, **kw):
    d = {'key': key, 'label': label, 'type': type_, 'required': required}
    d.update(kw)
    return d

# 试运行与归集这两栏最难写 —— 建设单位第一次报，不知道该写什么。
# 给一段照抄改数字的模板语句，比一句「请详细说明」有用得多
TRIAL_SAMPLE = (
    '2026年9月1日至11月30日试运行3个月，系统运行稳定，注册用户320个、日均访问460次，'
    '未发生影响业务的故障；遗留问题2项（报表导出格式、移动端适配），已列入运维计划。'
)
COLLECT_SAMPLE = (
    '已归集本系统产生的业务数据：数据库1个、业务表23张，累计约12万条；'
    '其中人员信息、项目信息、资金拨付记录已通过市数据共享平台归集入库，'
    '按《数据资源目录》挂接6个数据项，每日增量更新。'
)

# 其余几处常写的意见也给个模板：公文口径一致，省得每人一个写法
PRE_SAMPLE = (
    '经审核，申报材料齐全，建设内容与投资估算匹配，可研章节结构完整；'
    '同意提交评审会评审。'
)
COST_SAMPLE = (
    '经评估，项目送审投资 116.3 万元，核减至 108.6 万元，核减 7.7 万元（6.6%）；'
    '核减原因为软件功能模块重复计量，建设内容与投资规模匹配，建议按核减后金额立项。'
)
FISCAL_SAMPLE = (
    '经审核，项目已列入 2026 年市本级财政预算，安排资金 108.6 万元，'
    '其中当年安排 60 万元、结转下年 48.6 万元。'
)
APPROVAL_SAMPLE = (
    '已于 2026 年 11 月 20 日按模板出具《关于××项目立项的批复》并送达申报单位，'
    '批复投资按财政核定数填写。'
)
ACCEPT_SAMPLE = (
    '验收组听取建设情况汇报、查验系统功能与数据归集情况，认为已完成合同约定内容，'
    '功能与性能满足要求，同意通过验收。'
)
RETURN_SAMPLE = (
    '试运行情况只写了「运行正常」，缺起止时间与遗留问题；'
    '数据归集情况缺库表数量与归集方式，无法据以出具证明，请补充后重新申请。'
)


def _conclude_target(project, payload):
    """评审结论决定去哪，不由录入人选。

    通过 → 费用评审（交财评机构评估）；修改后通过 → 评审后补正（申报单位改材料）；
    不通过 → 终止。以前无论结论如何都转审核岗，等于把评审会否掉的
    项目再推给审核岗否一次，也让「修改后通过」的人以为改材料是自愿的。
    """
    result = (payload or {}).get('conclusion')
    if result == Project.ReviewResult.MODIFIED:
        return Project.Stage.REVIEW_SUPPLEMENT
    if result == Project.ReviewResult.REJECT:
        return Project.Stage.REJECTED
    return Project.Stage.COST_REVIEW


ACTIONS = [
    {
        'key': 'submit', 'label': '提交申报', 'roles': [APPLICANT],
        'stages': [Project.Stage.DRAFT], 'to': Project.Stage.PRECHECK,
        'confirm': '提交后材料锁定，进入预审。确认提交？',
        'form': [_f('note', '提交说明', required=False)],
    },
    {
        'key': 'withdraw', 'label': '撤回修改', 'roles': [APPLICANT],
        'stages': [Project.Stage.PRECHECK], 'to': Project.Stage.DRAFT,
        'confirm': '撤回后回到「申报中」，可继续修改材料。确认撤回？',
        'form': [_f('note', '撤回原因', required=True, min=5)],
    },
    {
        'key': 'rule_check', 'label': '执行规则预审', 'roles': [REVIEWER, ADMIN],
        'stages': [Project.Stage.PRECHECK], 'to': None,
        'form': [],
    },
    {
        'key': 'ai_review', 'label': 'AI 深度审查', 'roles': [REVIEWER, ADMIN],
        'stages': [Project.Stage.PRECHECK], 'to': None,
        'confirm': '将把申报材料按章提交给已配置的模型逐项审查，通常 1—2 分钟，'
                   '材料越长越久。执行期间请不要关闭或刷新页面。继续？',
        'form': [],
    },
    {
        'key': 'precheck_pass', 'label': '初审通过，转评审', 'roles': [REVIEWER, ADMIN],
        'stages': [Project.Stage.PRECHECK], 'to': Project.Stage.EXPERT_REVIEW,
        'confirm': '通过后进入专家评审，不能再退回补正。确认？',
        'form': [
            _f('note', '初审意见', required=True, min=10,
               sample=PRE_SAMPLE),
        ],
    },
    {
        'key': 'precheck_return', 'label': '退回补正', 'roles': [REVIEWER, ADMIN],
        'stages': [Project.Stage.PRECHECK], 'to': Project.Stage.SUPPLEMENT,
        'confirm': '退回后由申报单位修改重报，系统会生成补正单。确认退回？',
        'form': [
            _f('items', '补正事项', 'items', required=True),
            _f('deadline', '补正期限', 'date'),
            _f('note', '补充说明'),
        ],
    },
    {
        'key': 'resubmit', 'label': '补正后重新提交', 'roles': [APPLICANT],
        'stages': [Project.Stage.SUPPLEMENT], 'to': Project.Stage.PRECHECK,
        'confirm': '重新提交后进入预审。确认？',
        'form': [_f('note', '补正说明（可留空，系统会自动列出本轮改了什么）')],
    },
    {
        # 现实里是开评审会、专家在纸上签字，会后由会务把各家意见表和汇总表
        # 扫描上传、按三档票数录一遍。不在系统里给每位专家建账号：会都开完了，
        # 没人愿意再登一次，硬要求只会逼着大家共用账号，留痕反而更假
        'key': 'conclude', 'label': '录入评审会意见', 'roles': [REVIEWER, ADMIN],
        'stages': [Project.Stage.EXPERT_REVIEW], 'to': _conclude_target,
        # 结论决定去向，三个可能的目标都要预先准备承办人清单：
        # 通过转审核岗、修改后通过转申报单位、不通过直接归档
        'targets': [Project.Stage.COST_REVIEW, Project.Stage.REVIEW_SUPPLEMENT,
                    Project.Stage.REJECTED],
        'confirm': '系统按「通过（含修改后通过）过半」生成结论：通过转费用评审；'
                   '修改后通过转申报单位改材料，改完交费用评审；不通过即终止。确认？',
        'form': [
            _f('votes', '评审会票数', 'votes', required=True),
            _f('note', '评审会意见', required=True, min=10),
            _f('items', '修改要求（逐条列，仅结论为「修改后通过」时使用）', 'items'),
            _f('deadline', '修改期限（同上）', 'date'),
        ],
    },
    {
        'key': 'review_resubmit', 'label': '改完提交费用评审', 'roles': [APPLICANT],
        'stages': [Project.Stage.REVIEW_SUPPLEMENT], 'to': Project.Stage.COST_REVIEW,
        'confirm': '提交后转费用评审，不再回预审、不再上会。确认材料已按评审意见改完？',
        'form': [_f('note', '修改说明（可留空，系统会自动列出本轮改了什么）')],
    },
    {
        # 费用评审是第三方做的，结论就在机构出的报告里。审核岗只登记「通过」，
        # 不替机构写意见 —— 多一个意见框，实际只会抄一遍报告结论
        'key': 'cost_review_pass', 'label': '费用评审通过，转财政审核', 'roles': [AUDITOR, ADMIN],
        'stages': [Project.Stage.COST_REVIEW], 'to': Project.Stage.FISCAL_REVIEW,
        'confirm': '确认第三方《费用评估报告》已上传、结论为通过？通过后交财政审核资金安排。',
        'form': [
            # 结论之外必须落一个数：批复里写的总投资取这里，不取可研。
            # 默认带出可研投资，经办人按报告改成核定数
            _f('audit_amount', '评估核定投资（万元）', 'number', required=True,
               tip='按《费用评估报告》核定的总投资填；这里是批复金额的来源，'
                   '财政审核时如有调整可在下一步改'),
        ],
    },
    {
        # 「退回补正」听起来像材料被否了；实际是财评机构出了评估意见，
        # 请单位确认有无异议、要不要补依据，用「征求反馈」更中性
        'key': 'cost_review_return', 'label': '征求反馈', 'roles': [AUDITOR, ADMIN],
        'stages': [Project.Stage.COST_REVIEW], 'to': Project.Stage.COST_SUPPLEMENT,
        'confirm': '把评估意见发给申报单位，请其确认有无异议、是否补交依据。'
                   '反馈完直接回费用评审重评，不重走预审、不重新上会。确认发出？',
        'form': [
            _f('items', '需要单位反馈的事项', 'items', required=True),
            _f('deadline', '反馈期限', 'date'),
            _f('note', '费用评估意见', required=True, min=10,
               sample=COST_SAMPLE),
        ],
    },
    {
        # 单位反馈回费用评审：有异议就写清理由并附佐证，没异议可直接确认或补依据。
        # 不能把这步并进「预审补正」—— 那会退回预审重跑、重开评审会。
        # 理由不做必填：审核岗退回多半是要补计价依据，单位照办本来就是「无异议」，
        # 逼着凑满十个字，只会留下一堆「已按要求补充材料」的废话。
        'key': 'cost_resubmit', 'label': '提交反馈，回费用评审', 'roles': [APPLICANT],
        'stages': [Project.Stage.COST_SUPPLEMENT], 'to': Project.Stage.COST_REVIEW,
        'confirm': '提交后回到费用评审，由审核岗按现有材料重新评估。'
                   '没有异议、也没有材料要补的，不用填任何内容，直接提交即可。确认？',
        'form': [
            _f('note', '反馈说明（选填）', min=10,
               placeholder='有异议或需要解释时在这里写，不少于 10 个字；'
                           '没有异议、也无需补充材料的，留空直接提交',
               tip='留空即视为「对评估结论无异议，无需补充材料」，系统按此留痕，不需要凑字数。'),
        ],
    },
    {
        'key': 'cost_review_reject', 'label': '费用评审不通过', 'roles': [AUDITOR, ADMIN],
        'stages': [Project.Stage.COST_REVIEW], 'to': Project.Stage.REJECTED,
        'confirm': '费用评审不通过是终态，项目将关闭。确认？',
        'form': [_f('note', '不通过理由', required=True, min=10)],
    },
    {
        'key': 'fiscal_pass', 'label': '财政同意，转立项', 'roles': [AUDITOR, ADMIN],
        'stages': [Project.Stage.FISCAL_REVIEW], 'to': Project.Stage.APPROVAL,
        'confirm': '确认财政局已同意安排资金？通过后进入立项环节，出具立项批复。',
        'form': [
            _f('note', '财政审核意见（资金安排）', required=True, min=10,
               placeholder='写清资金安排的结论，如：已列入 2026 年市本级财政预算',
               sample=FISCAL_SAMPLE),
            # 财政定的数为准：默认带出财评核定数，财政有调整就在这里改
            _f('budget_amount', '财政核定投资（万元）', 'number',
               tip='默认带出费用评审核定的金额；财政核减或核增时在这里改，'
                   '立项批复按这个数写。不填即沿用财评结论'),
        ],
    },
    {
        'key': 'fiscal_reject', 'label': '财政无资金，转储备库', 'roles': [AUDITOR, ADMIN],
        'stages': [Project.Stage.FISCAL_REVIEW], 'to': Project.Stage.RESERVE,
        'confirm': '确认财政无资金安排？项目将转入项目储备库，不进入招标。',
        'form': [_f('note', '财政审核意见（无资金安排的原因）', required=True, min=10)],
    },
    {
        'key': 'approval_issue', 'label': '批复已出具，转待招标', 'roles': [REVIEWER, ADMIN],
        'stages': [Project.Stage.APPROVAL], 'to': Project.Stage.TENDERING,
        'confirm': '确认立项批复已按模板生成并送达申报单位？确认后进入待招标。',
        'form': [
            # 批复本身不带文号（模板里已去掉），这里也不再收文号：
            # 文号是发文部门给的，多数时候批复还没编号就要转下一环节，
            # 逼着填只会让人随便编一串数字进档案
            _f('note', '立项说明（送达情况）', required=True, min=10,
               sample=APPROVAL_SAMPLE),
        ],
    },
    {
        'key': 'start_build', 'label': '招标完成，转建设中', 'roles': [REVIEWER, ADMIN],
        'stages': [Project.Stage.TENDERING], 'to': Project.Stage.BUILDING,
        'confirm': '确认招标已完成、项目已进入建设？'
                   '中标通知书与合同由建设单位和招标代理自行保管，本系统不再收存。',
        'form': [],
    },
    {
        'key': 'reserve_reactivate', 'label': '重新纳入财政审核', 'roles': [AUDITOR, ADMIN],
        'stages': [Project.Stage.RESERVE], 'to': Project.Stage.FISCAL_REVIEW,
        'confirm': '确认财政资金已安排、重新提交财政审核？',
        'form': [_f('note', '重新纳入理由', required=True, min=10)],
    },
    {
        # 建设过程的进度、初验、变更都是建设单位自己的事，本系统只记「到哪一步了」，
        # 不再要求报过程档案 —— 收了也没人核，反而把责任揽到我局身上
        'key': 'start_trial', 'label': '建设完成，转试运行', 'roles': [REVIEWER, ADMIN],
        'stages': [Project.Stage.BUILDING], 'to': Project.Stage.TRIAL,
        'confirm': '确认项目已建设完成、转由建设单位试运行？'
                   '转出后由申报单位填报试运行情况与数据归集情况，再申请验收。',
        'form': [],
    },
    {
        # 试运行是建设单位的事：运行得怎么样、数据归集到哪一步，只有他们说得清。
        # 这两段文字不是走形式 —— 待验收时我局据此出具《数据归集证明》，
        # 也就是日后追责时「凭什么说数据已归集」的原始依据
        'key': 'apply_acceptance', 'label': '提交试运行情况，申请验收',
        'roles': [APPLICANT, ADMIN],
        'stages': [Project.Stage.TRIAL], 'to': Project.Stage.ACCEPTANCE,
        'confirm': '提交后进入待验收。我局将据此出具《数据归集证明》，'
                   '没有归集不能验收；验收后专家意见报我局备案。',
        'form': [
            _f('trial_summary', '试运行情况', required=True, min=15,
               placeholder='写清试运行起止时间、运行是否稳定、有没有遗留问题',
               sample=TRIAL_SAMPLE,
               tip='起止时间、运行是否稳定、遗留问题，三样写全 —— 验收会上要看。'),
            _f('data_collect_scope', '数据归集情况', required=True, min=10,
               placeholder='写清归集了哪些数据：系统、数据库/库表、数据量、归集方式',
               sample=COLLECT_SAMPLE,
               tip='这一段会原样写进《数据归集证明》。归集到市数据共享平台的要写明平台名称。'),
            _f('note', '验收申请说明（选填）', min=10,
               placeholder='如：已完成合同约定内容，具备验收条件，申请组织专家验收'),
        ],
    },
    {
        # 待验收只有一道硬门槛：《数据归集证明》已按模板出具（我局出）。
        # 没有归集就验收，等于放行「建了不用、数据另起一套」的项目。
        # 专家验收意见不再卡在这一步 —— 它要等验收会开完才有，属于结项后的备案件
        'key': 'accept', 'label': '验收确认，项目结项', 'roles': [AUDITOR, ADMIN],
        'stages': [Project.Stage.ACCEPTANCE], 'to': Project.Stage.ACCEPTED,
        'confirm': '确认验收合格、项目结项？结项后把专家验收意见上传备案（结项不以它为前置）。',
        'form': [_f('note', '验收情况（可留空，验收结论以专家验收意见为准）', min=10,
               sample=ACCEPT_SAMPLE)],
    },
    {
        # 单位报的试运行或归集情况说不清、证明出不来时，退回重填，
        # 别让审核岗在「缺数据」和「卡住不动」之间硬选一个
        'key': 'accept_return', 'label': '退回补充试运行与归集说明',
        'roles': [AUDITOR, ADMIN],
        'stages': [Project.Stage.ACCEPTANCE], 'to': Project.Stage.TRIAL,
        'confirm': '退回后由申报单位补充试运行情况与数据归集情况，再重新申请验收。',
        'form': [_f('note', '退回理由（写清缺什么）', required=True, min=10,
               sample=RETURN_SAMPLE)],
    },
]

ACTION_MAP = {a['key']: a for a in ACTIONS}

# ---------------------------------------------------------------- 保存（在办）
# 「保存」不是流转动作，所以不进 ACTIONS：它不改阶段、不进留痕、不影响别人的
# 待办，只把件从待办箱挪进在办箱 —— 与「动作填一半存草稿」是同一张表。
#
# 为什么需要它：费用评审要委托财评机构，现场给的是 7 个自然日。任务书送达后
# 报告还没回来，这段时间件本来就在我手上；没有保存，它要么一直挂在待办里被
# 反复催，要么只能被逼着提前转走 —— 两种都不对。
HOLD_KEY = 'hold'
# 保存只留「为什么停在这儿」与费用评审的几个参数：财评机构、期限这些是
# 任务书的输入，回头接着办时要能带回来，不必凭记忆重填
HOLD_FIELDS = {'note', 'deadline', 'entrust_unit', 'entrust_contact',
               'entrust_phone', 'unit_contact', 'unit_phone', 'days'}

HOLD_LABEL = {
    Project.Stage.COST_REVIEW: '保存，等第三方评估报告',
}
HOLD_LABEL_DEFAULT = '保存，等本环节办结'


def hold_label(stage):
    return HOLD_LABEL.get(stage, HOLD_LABEL_DEFAULT)


def hold_block_reason(project):
    """保存之前的硬门槛，返回空串表示可以保存。

    费用评审必须先有《费用评审任务书》：没把件送出去就说「在等报告」，
    在办箱里挂的是一句假话，谁接手都说不清这 7 天到底在等什么。
    """
    if project.stage == Project.Stage.COST_REVIEW:
        if not project.documents.filter(kind='cost_review_task').exists():
            return ('先在本环节生成《费用评审任务书》并送达财评机构，再点保存 —— '
                    '没送出去的件没有「等报告」这回事。')
    return ''



def assign_options(project, to_stage):
    """某个目标环节的承办人候选。

    申报单位的环节不列人：一个项目只有一个申报账号，交回单位就是交回那个人。
    局内环节把该权限域下所有启用账号列出来，转出时选一个；一个都不选时
    落成「岗位待办」，该岗位任何人都能接（有人休假、岗位临时换人时不至于卡死）。
    """
    scope = STAGE_ROLE.get(to_stage, '')
    if not scope or to_stage in TERMINAL_STAGES:
        return None
    label = Project.Stage(to_stage).label
    if scope == APPLICANT:
        u = project.applicant
        return {'stage': to_stage, 'label': label, 'scope': scope,
                'scope_label': ROLE_LABEL.get(scope, scope), 'fixed': True,
                'candidates': [{'id': u.pk, 'display': u.display,
                                'role_label': u.role_label, 'unit': u.unit or ''}] if u else []}
    users = (User.objects.filter(is_active=True, role_ref__scope=scope)
             .select_related('role_ref').order_by('id'))
    return {'stage': to_stage, 'label': label, 'scope': scope,
            'scope_label': ROLE_LABEL.get(scope, scope), 'fixed': False,
            'candidates': [{'id': u.pk, 'display': u.display,
                            'role_label': u.role_label, 'unit': u.unit or ''}
                           for u in users]}


def _target_stages(a):
    to = a.get('to')
    if isinstance(to, str):
        return [to] if to else []
    return list(a.get('targets') or [])


def _public_action(a, project, user, with_assign=False):
    """下发给前端的动作描述，去掉 Python 里的判断函数。"""
    data = {
        'key': a['key'], 'label': a['label'], 'confirm': a.get('confirm', ''),
        'to': a['to'] if isinstance(a.get('to'), str) else '',
        'form': a.get('form', []),
    }
    if with_assign:
        opts = [assign_options(project, s) for s in _target_stages(a)]
        data['assign'] = [o for o in opts if o]
    return data


def available_actions(project, user, with_assign=False):
    """当前用户此刻能对项目做什么。前端只渲染这个列表，不自己判断角色。

    with_assign 只在详情页打开：待办列表每个项目都带一份人员清单，
    件一多就是几十次多余查询。
    """
    out = []
    # 环节指定了具体岗位时，只该由这个岗位的人办（管理员兜底）。
    # 只查一次，别在下面的循环里对每个动作查一遍库
    override = _stage_role_override(project.stage)
    my_role_code = getattr(getattr(user, 'role_ref', None), 'code', '') or ''
    for a in ACTIONS:
        if project.stage not in a['stages']:
            continue
        if user.role not in a['roles']:
            continue
        if user.role == APPLICANT and project.applicant_id != user.pk:
            continue
        if override and user.role != ADMIN and my_role_code != override:
            continue
        # 撤回只允许在初审还没动手时用：初审岗已经跑过预审、存过稿，
        # 撤回等于把他做过的活作废，还会让同一份材料被两个人各审一遍
        if a['key'] == 'withdraw' and _precheck_started(project):
            continue
        out.append(_public_action(a, project, user, with_assign))
    return _mark_primary(out, project, user)


def _precheck_started(project):
    """初审是否已经动手：出过预审报告，或初审岗在预审环节存过草稿。

    只判「有没有预审记录」不够 —— 初审岗跑到一半、报告还没出，这时候
    单位撤回，他刚写完的意见就全废了。
    """
    if project.reports.exists():
        return True
    return ActionDraft.objects.filter(project=project,
                                      stage=Project.Stage.PRECHECK).exists()


def _mark_primary(out, project, user):
    """标出「下一步该点哪个」。

    预审阶段四个按钮并排，如果一律把第一个当主按钮，做完规则预审后
    界面还高亮「执行规则预审」，等于催人重复劳动。按是否已有本轮报告来定。
    """
    if not out:
        return out
    keys = {a['key'] for a in out}
    pick = None

    if project.stage == Project.Stage.PRECHECK:
        # 一次查出本轮已有的报告级别，别在待办列表里对每个项目查两遍
        levels = set(project.reports.filter(round=project.round)
                     .values_list('level', flat=True))
        has_rule = 'rule' in levels
        has_ai = 'ai' in levels
        if 'rule_check' in keys and not has_rule:
            pick = 'rule_check'
        elif 'ai_review' in keys and not has_ai:
            pick = 'ai_review'
        elif 'precheck_pass' in keys:
            pick = 'precheck_pass'
    elif project.stage == Project.Stage.EXPERT_REVIEW and 'conclude' in keys:
        # 评审阶段只剩录票这一个动作，主按钮就是它
        pick = 'conclude'
    elif project.stage == Project.Stage.SUPPLEMENT and 'resubmit' in keys:
        pick = 'resubmit'
    elif project.stage == Project.Stage.REVIEW_SUPPLEMENT and 'review_resubmit' in keys:
        pick = 'review_resubmit'
    elif project.stage == Project.Stage.COST_REVIEW and 'cost_review_pass' in keys:
        # 费用评审先出任务书、再登记意见，主按钮仍是「通过转财政审核」
        pick = 'cost_review_pass'
    elif project.stage == Project.Stage.COST_SUPPLEMENT and 'cost_resubmit' in keys:
        pick = 'cost_resubmit'
    elif project.stage == Project.Stage.FISCAL_REVIEW and 'fiscal_pass' in keys:
        pick = 'fiscal_pass'
    elif project.stage == Project.Stage.APPROVAL and 'approval_issue' in keys:
        pick = 'approval_issue'
    elif project.stage == Project.Stage.RESERVE and 'reserve_reactivate' in keys:
        pick = 'reserve_reactivate'
    elif project.stage == Project.Stage.DRAFT and 'submit' in keys:
        pick = 'submit'

    for i, a in enumerate(out):
        a['primary'] = (a['key'] == pick) if pick else (i == 0)
    return out


# ------------------------------------------------------------------ 校验工具
def _text(data, key, label, min_len=0, required=False):
    v = (data.get(key) or '').strip()
    if required and not v:
        raise FlowError(f'请填写{label}')
    if min_len and v and len(v) < min_len:
        raise FlowError(f'{label}至少 {min_len} 个字，太短的说明等于没写')
    return v


def _int(data, key, label, required=False, low=None, high=None):
    raw = data.get(key)
    if raw in (None, ''):
        if required:
            raise FlowError(f'请填写{label}')
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise FlowError(f'{label}必须是整数')
    if low is not None and v < low:
        raise FlowError(f'{label}不能小于 {low}')
    if high is not None and v > high:
        raise FlowError(f'{label}不能大于 {high}')
    return v


def _money(data, key, label, required=False):
    """取一个金额（万元）。空串当没填；非正数直接拒 —— 0 元立项没有意义。"""
    raw = data.get(key)
    if raw in (None, ''):
        if required:
            raise FlowError(f'请填写{label}')
        return None
    try:
        v = Decimal(str(raw).replace(',', '').strip())
    except (TypeError, ValueError, InvalidOperation):
        raise FlowError(f'{label}必须是数字（万元）')
    if v <= 0:
        raise FlowError(f'{label}必须大于 0')
    return v.quantize(Decimal('0.01'))


def _date(data, key, label):
    """取一个日期字段。非法值直接拒，别把 ValidationError 漏成 500。"""
    raw = (data.get(key) or '')
    if hasattr(raw, 'strip'):
        raw = raw.strip()
    if not raw:
        return None
    parsed = parse_date(str(raw))
    if parsed is None:
        raise FlowError(f'{label}格式不正确，应填 2006-01-02 这样的日期')
    return parsed


def _items(data, key, label, min_count=1):
    raw = data.get(key) or []
    if not isinstance(raw, list):
        raise FlowError(f'{label}格式不正确')
    out = []
    for it in raw:
        if isinstance(it, str):
            it = {'title': it}
        title = (it.get('title') or '').strip()
        if len(title) < 4:
            raise FlowError(f'{label}每条至少 4 个字，要写清具体改什么')
        out.append({'title': title,
                    'requirement': (it.get('requirement') or '').strip()})
    if len(out) < min_count:
        raise FlowError(f'请至少填写 {min_count} 条{label}')
    return out


def _votes(data):
    """评审会三档票数。

    票数就是评审会的全部事实，所以只认三个整数。原来的做法是先指定
    「几位专家账号」、再逐人录结论，等于为了记录三个数字先维护一套人员库 ——
    评审专家大多不在本单位、也不该有系统账号，这套人员库没人愿意维护，
    最后一定是临时编几个名字填进去。
    """
    raw = data.get('votes')
    if isinstance(raw, dict):
        vals = {k: raw.get(k) for k in ('agree', 'modified', 'reject')}
    elif isinstance(raw, list) and raw and isinstance(raw[0], dict):
        # 兼容前端按 [{key, value}] 提交的写法
        vals = {k: next((x.get('value') for x in raw if x.get('key') == k), None)
                for k in ('agree', 'modified', 'reject')}
    else:
        vals = {}
    out = {}
    for key, label in (('agree', '通过'), ('modified', '修改后通过'),
                       ('reject', '不通过')):
        v = _int(vals, key, label, required=True)
        if v < 0:
            raise FlowError(f'「{label}」票数不能为负数')
        out[key] = v
    total = sum(out.values())
    if total <= 0:
        raise FlowError('三档票数合计为 0，评审会不可能一个人都没投票')
    if total % 2 == 0:
        # 4 票可能出现 2:2，既不能算通过也不能算不通过，流程当场卡住
        raise FlowError(f'投票合计 {total} 人为偶数，会出现平票。'
                        '评审会应为单数（3、5、7…），请核对签字表后重填')
    out['total'] = total
    return out


def _latest_report(project, level, round_=None):
    qs = project.reports.filter(level=level)
    if round_ is not None:
        qs = qs.filter(round=round_)
    return qs.order_by('-created').first()


# ------------------------------------------------------------------ 执行入口
def _resolve_handler(project, to_stage, assignee_id, actor):
    """算出这一件转出去之后落在谁桌上。

    - 申报单位的环节固定回申报账号，不让人选（一个项目只有一个申报人）；
    - 局内环节按传入的 assignee 落库；没传时，同一个岗位的经办人默认自留，
      否则落成「岗位待办」，该岗位任何人打开都能办；
    - 归档 / 储备 / 不予通过没有承办人，一并清空，免得台账上挂着一个办事的人。
    """
    scope = STAGE_ROLE.get(to_stage, '')
    if not scope:
        return None
    if scope == APPLICANT:
        return project.applicant
    if assignee_id:
        user = (User.objects.filter(pk=assignee_id, is_active=True,
                                    role_ref__scope=scope)
                .select_related('role_ref').first())
        if not user:
            raise FlowError('所选承办人不属于该环节的办理岗位，请刷新后重新选择')
        return user
    return actor if actor.role == scope else None


@transaction.atomic
def transition(project, user, key, data=None):
    data = data or {}
    spec = ACTION_MAP.get(key)
    if not spec:
        raise FlowError('未知的流转动作')
    if project.stage not in spec['stages']:
        raise FlowError(
            f'项目当前处于「{project.get_stage_display()}」，不能执行「{spec["label"]}」。'
            '请刷新页面看最新状态。')
    if user.role not in spec['roles']:
        raise FlowError(f'「{spec["label"]}」只能由'
                        f'{"、".join(ROLE_LABEL[r] for r in spec["roles"])}办理')
    if user.role == APPLICANT and project.applicant_id != user.pk:
        raise FlowError('只能操作本单位申报的项目')

    # 承办人是真正的办理权（用户拍板 #17 A）：局内岗位只能办转给自己的件。
    # 管理员不受此限——改派、纠错要能从任意件入手；存量件 handler 为空时
    # 按岗位池处理，任何该岗位的人都能领，避免历史件突然无人能办。
    if (user.role != ADMIN and user.role != APPLICANT
            and project.handler_id and project.handler_id != user.pk):
        raise FlowError(
            f'本件当前承办人是「{project.handler_name}」，'
            '只有承办人本人或管理员可以办理；需要接手请联系管理员改派')

    project = Project.objects.select_for_update().get(pk=project.pk)
    # 加锁前用的是视图传进来的旧对象，锁后必须拿数据库里的最新状态复核：
    # 两个请求同时点"录入评审会意见"时，后到的一个会读到已被前一个改过的 stage，
    # 不复核就会重复执行 round+1 / 生成两张补正单。
    # 注：SQLite 的 select_for_update 是空操作，但复核本身不依赖锁 —— 即使
    # 在 SQLite 上，只要 handler_fn 前重新校验，也能挡住绝大多数并发重入。
    if project.stage not in spec['stages']:
        raise FlowError('项目状态已被其他操作更新，请刷新页面后重试')
    # 锁后复核承办人：并发改派时不能用锁前的旧 handler 放行
    if (user.role != ADMIN and user.role != APPLICANT
            and project.handler_id and project.handler_id != user.pk):
        raise FlowError('本件已改派给其他承办人，请刷新页面后重试')
    from_stage = project.stage
    handler_fn = _HANDLERS[key]
    payload, extra = handler_fn(project, user, data)

    to_spec = spec.get('to')
    # 结论类动作的去向由票数决定，spec 里写 callable，处理完再算
    to_stage = to_spec(project, payload) if callable(to_spec) else to_spec
    assignee = None
    if to_stage:
        assignee = _resolve_handler(project, to_stage, data.get('assignee'), user)
        fields = {'handler': assignee}
        if assignee is not None:
            fields['handler_role'] = assignee.role_code
        project.set_stage(to_stage, fields)
    else:
        # 规则预审 / AI 审查这类 to=None 的动作仍在当前环节，不是"进入新环节"。
        # 原来这里把 stage_at 刷成当前时间，导致停留天数与 SLA 倒计时归零，
        # 超期前反复点一下就能规避超时告警。handler_fn 内部已自行 save 业务字段，
        # 这里只更新 updated 留痕，绝不动 stage_at。
        project.save(update_fields=['updated'])

    if assignee is not None:
        # 谁把件交给谁，是流程留痕的一部分：事后追「为什么压在他那」只有这条线索
        extra['assignee'] = assignee.display
    _log(project, user, key, spec['label'], from_stage, project.stage,
         extra.pop('comment', ''), {**payload, **extra})
    # 件已经往下走了，这一环节的半成品草稿作废：留着会在「在办箱」里
    # 挂一件已经办过的件，点进去还提示接着填上一步的表
    ActionDraft.objects.filter(project=project).delete()
    return project


def _log(project, user, action, label, from_stage, to_stage, comment, payload):
    ProjectEvent.objects.create(
        project=project, action=action, action_label=label,
        from_stage=from_stage, to_stage=to_stage or '',
        actor=user, actor_name=user.display, actor_role=user.role,
        actor_role_label=user.role_label, comment=comment,
        payload=payload or {},
    )


# ------------------------------------------------------------------ 各动作实现
def _h_submit(project, user, data):
    note = _text(data, 'note', '提交说明')
    if len(project.name.strip()) < 4:
        raise FlowError('项目名称太短，请填写完整名称（至少 4 个字）')
    if not project.unit.strip():
        raise FlowError('请填写申报单位')
    # 单位必须是市本级名录里的标准写法，否则按单位统计口径就散了
    from apps.core import org_units

    std, known = org_units.normalize(project.unit, allow_inactive=False)
    if not known:
        raise FlowError(
            f'申报单位「{project.unit}」不在市本级单位名录内。请打开项目信息'
            '重新选择申报单位后再提交；如属新设机构，请联系管理员补充名录')
    if std != project.unit:
        project.unit = std
        project.save(update_fields=['unit', 'updated'])
    if not project.contact_name.strip():
        raise FlowError('请填写联系人：件到了、被退回、评审有结论，短信都发给这个人')
    if not project.contact_phone.strip():
        raise FlowError('请填写联系人手机号：待办提醒按这个号码发短信，不填通知不到单位')
    import re

    if not re.fullmatch(r'1[3-9]\d{9}', project.contact_phone.strip().replace(' ', '')):
        raise FlowError('联系人手机号不是 11 位手机号：这一栏用来发短信通知，'
                        '座机收不到，请改成手机号后再提交')
    if not project.investment or project.investment <= 0:
        raise FlowError('请填写总投资，投资额为 0 的项目无法进入预审')
    if len(project.summary.strip()) < 30:
        raise FlowError('建设内容说明至少 30 个字，否则预审无从对照')
    files = project.files.filter(status='ready')
    if not files.exists():
        raise FlowError('还没有解析成功的材料。请上传可研报告或项目建议书（PDF/DOCX），'
                        '解析完成后才能提交')
    if not files.filter(doc_type__in=['feasibility', 'proposal']).exists():
        raise FlowError('缺少可行性研究报告或项目建议书，只有概算或批复不能作为申报依据')

    # 提交即做一次重复建设筛查，把结果挂在项目上，预审页直接能看到
    from . import precheck
    dup = precheck.screen_duplicate(project)
    if project.submitted_at is None:
        project.submitted_at = timezone.now()
    project.dup_check = dup
    project.save(update_fields=['submitted_at', 'dup_check', 'updated'])
    return ({'files': files.count(), 'dup_level': dup.get('level', '')},
            {'comment': note})


def _h_withdraw(project, user, data):
    note = _text(data, 'note', '撤回原因', min_len=5, required=True)
    if project.reports.exists():
        raise FlowError('预审已经开始（已有预审记录），不能撤回；'
                        '如需修改材料，请联系初审岗退回补正')
    return ({}, {'comment': note})


def _h_rule_check(project, user, data):
    from . import precheck
    report, dup = precheck.run_rule_check(project, user)
    project.dup_check = dup
    project.save(update_fields=['dup_check', 'updated'])
    c = report.counts or {}
    return ({'report_id': report.id, 'verdict': report.verdict,
             'block': c.get('block', 0), 'warn': c.get('warn', 0),
             'pass': c.get('pass', 0)},
            {'comment': f'一级预审：必须补正 {c.get("block", 0)} 项，'
                        f'需说明 {c.get("warn", 0)} 项'})


def _h_ai_review(project, user, data):
    from . import precheck
    report = precheck.run_ai_review(project, user)
    return ({'report_id': report.id, 'verdict': report.verdict,
             'findings': len(report.findings or [])},
            {'comment': f'AI 深度审查：{len(report.findings or [])} 条问题'})


def _h_precheck_pass(project, user, data):
    note = _text(data, 'note', '初审意见', min_len=10, required=True)

    rule = _latest_report(project, PrecheckReport.Level.RULE, project.round)
    if not rule:
        raise FlowError('还没做一级规则预审。请先点「执行规则预审」，'
                        '有硬性缺项的必须先退回补正，不能直接通过')
    if rule.verdict == PrecheckReport.Verdict.BLOCK:
        n = (rule.counts or {}).get('block', 0)
        raise FlowError(f'一级预审有 {n} 项「必须补正」，不能带病上会。'
                        '请先退回补正，让申报单位补齐后再预审')

    # AI 审查若判了「必须补正」，不硬拦：模型会误判，拦死了流程就卡在初审岗。
    # 但必须随件流转并写进留痕，让专家和审核岗知道模型提了什么，不能悄悄放过去。
    ai = _latest_report(project, PrecheckReport.Level.AI, project.round)
    ai_block = (ai.counts or {}).get('block', 0) if ai else 0

    payload = {'rule_report': rule.id,
               'rule_block': (rule.counts or {}).get('block', 0),
               'rule_warn': (rule.counts or {}).get('warn', 0),
               'ai_report': ai.id if ai else None,
               'ai_block': ai_block}
    if ai_block:
        payload['ai_note'] = f'AI 审查另有 {ai_block} 项「必须补正」意见，已随件转评审会'
    return payload, {'comment': note}


def _h_precheck_return(project, user, data):
    items = _items(data, 'items', '补正事项')
    note = _text(data, 'note', '补充说明')
    deadline = _date(data, 'deadline', '补正期限')

    project.round += 1
    project.save(update_fields=['round', 'updated'])
    SupplementNotice.objects.create(
        project=project, round=project.round,
        kind=SupplementNotice.Kind.PRECHECK, items=items, note=note,
        deadline=deadline, issued_by=user, status=SupplementNotice.Status.OPEN,
    )
    return ({'items': items, 'deadline': str(deadline or ''), 'round': project.round},
            {'comment': note or f'退回补正 {len(items)} 项'})


def _h_resubmit(project, user, data):
    # 补正说明可以不填：改了什么系统自己算得出来（段落级对照），
    # 手填「补了 3 条」既对不上实际改动，也白让申报人抄一遍
    # 与 review_resubmit 同一口径：什么都没改就点重新提交，补正单会被
    # 空手关掉、项目带病回预审（审查项 #15）。系统自动算改动，所以
    # 「改了什么」看得见；这里只要求本轮至少有一份解析成功的新材料。
    if not project.files.filter(round=project.round,
                                status=ProjectFile.Status.READY).exists():
        raise FlowError('本轮还没有上传解析成功的材料。先把补正材料传上来再提交，'
                        '否则补正单会空手关闭，说不清改了什么')
    note = _text(data, 'note', '补正说明')
    open_notices = project.notices.filter(status=SupplementNotice.Status.OPEN)
    # 必须先数再改：update() 之后 QuerySet 不会缓存旧结果，
    # 再 count() 查的是已经变成 resolved 的行，永远是 0
    resolved = open_notices.count()

    from . import diff as diffmod
    changes = diffmod.summarize_round(project)
    auto = '；'.join(f'{c["file"]}：{c["summary"]}' for c in changes)
    reply_text = note or auto or '本轮未上传新材料'
    if note and auto:
        reply_text = f'{note}\n系统比对：{auto}'

    open_notices.update(status=SupplementNotice.Status.RESOLVED,
                        reply=reply_text, resolved_at=timezone.now())
    return ({'resolved_notices': resolved, 'changes': changes},
            {'comment': reply_text})


def _h_conclude(project, user, data):
    """录入评审会意见。

    口径（2026-11-19 定）：「通过 + 修改后通过」超过半数即评审通过；
    有人投「修改后通过」的，结论记「修改后通过」，申报单位须按意见改完
    再交审核 —— 改材料是这一步的必经动作，不是可选项。
    结论由票数算，不让录入人自己选 —— 手选等于给了一个绕开票数的口子，
    而签字表上的签字才是评审判据。
    """
    note = _text(data, 'note', '评审会意见', min_len=10, required=True)
    if not project.review_files.exists() and not project.review_scan:
        raise FlowError('请先上传评审会材料（各专家意见表、汇总意见表，PDF 或图片）。'
                        '签字件是这套结论的原始凭证，不能只录几个数字')

    v = _votes(data)
    agree, modified, reject = v['agree'], v['modified'], v['reject']
    total = v['total']
    yes = agree + modified
    if yes * 2 > total:
        conclusion = (Project.ReviewResult.MODIFIED if modified
                      else Project.ReviewResult.PASS)
    else:
        conclusion = Project.ReviewResult.REJECT

    project.review_result = conclusion
    project.review_note = note
    project.review_agree = agree
    project.review_modified = modified
    project.review_reject = reject
    project.concluded_at = timezone.now()

    notice = None
    if conclusion == Project.ReviewResult.MODIFIED:
        # 结论先落库，再开一轮补正：项目带着「修改后通过」的结论停在
        # 补正阶段等人改材料，不是一算完就跳走
        items = _items(data, 'items', '修改要求', min_count=0)
        if not items:
            # 没逐条列就不硬凑：把评审会意见整段作为一条要求，
            # 申报单位至少知道改什么，不至于收到一张空单子
            items = [{'title': '按评审会意见修改申报材料', 'requirement': note}]
        deadline = _date(data, 'deadline', '修改期限')
        project.round += 1
        notice = SupplementNotice.objects.create(
            project=project, round=project.round,
            kind=SupplementNotice.Kind.REVIEW, items=items, note=note,
            deadline=deadline, issued_by=user,
            status=SupplementNotice.Status.OPEN)

    project.save(update_fields=['review_result', 'review_note', 'review_agree',
                                'review_modified', 'review_reject',
                                'concluded_at', 'round', 'updated'])
    # 结论是算出来的，所以票数必须跟意见一起落到时间轴上 —— 只存进 payload，
    # 页面不展开就看不见，事后没人说得清这个结论是怎么来的
    vote_line = (f'通过 {yes}/{total}（通过 {agree}、修改后通过 {modified}、'
                 f'不通过 {reject}）')
    tail = ''
    if conclusion == Project.ReviewResult.MODIFIED:
        tail = f'\n已转申报单位修改材料（第 {project.round} 轮），改完交费用评审'
    elif conclusion == Project.ReviewResult.REJECT:
        tail = '\n评审会未通过，项目终止'
    return ({'conclusion': conclusion,
             'conclusion_label': project.get_review_result_display(),
             'votes': {'agree': agree, 'modified': modified, 'reject': reject,
                       'total': total, 'yes': yes},
             'notice': notice.id if notice else None,
             'round': project.round,
             'files': project.review_files.count()},
            {'comment': f'{note}\n{vote_line}{tail}', 'vote': vote_line})


def _h_review_resubmit(project, user, data):
    """评审会要求修改后，申报单位改完材料提交费用评审。

    和预审补正的区别只有一处：这里改的是「评委已经认了、只差材料」的东西，
    所以不再重跑预审、不再上会，直接进费用评审。非要重走一遍，等于让同一批人
    把同一个结论再表决一次。
    """
    note = _text(data, 'note', '修改说明')
    # 必须真有本轮新材料：只写一段「已按要求修改」不算改过，
    # 审核岗打开看到还是老版本，回头又得退一次
    if not project.files.filter(round=project.round, status='ready').exists():
        raise FlowError('还没有上传修改后的材料。请先在「申报材料」里上传'
                        '修改后的可研报告（与原文件同名会自动升为新的版本）')

    open_notices = project.notices.filter(status=SupplementNotice.Status.OPEN,
                                          kind=SupplementNotice.Kind.REVIEW)
    resolved = open_notices.count()

    from . import diff as diffmod
    changes = diffmod.summarize_round(project)
    auto = '；'.join(f'{c["file"]}：{c["summary"]}' for c in changes)
    reply_text = note or auto or '本轮未上传新材料'
    if note and auto:
        reply_text = f'{note}\n系统比对：{auto}'

    open_notices.update(status=SupplementNotice.Status.RESOLVED,
                        reply=reply_text, resolved_at=timezone.now())
    return ({'resolved_notices': resolved, 'changes': changes},
            {'comment': reply_text})


def _require_doc(project, kind, action_label):
    """对外文书是这一步的交付物：没生成就不许往下走。

    费用评审要先把《费用评审任务书》交财评机构，立项要先把《立项批复》
    发给申报单位。只靠提示语，实际一定有人跳过；写成硬约束才对得上纸面流程。
    """
    if not project.documents.filter(kind=kind).exists():
        raise FlowError(
            f'还没有生成{action_label}。请先在页面顶部「本环节办理」里点生成，'
            '下载并送达后再回来办这一步')


def _h_cost_review_pass(project, user, data):
    """费用评审通过：只认第三方报告，不让审核岗代写意见。

    费用评审是财评机构做的，结论、金额、理由都写在《费用评估报告》里。
    原来还要求审核岗填一段「费用评审意见」，实际只能把报告结论抄一遍，
    抄漏了反而看不出分歧。现在改成报告必须上传、意见由报告承担：
    没有报告就记「通过」，后面财政审核手里没有依据，等于空转。

    报告允许是扫描件（机构交来的多是签章件），所以只校验「有这份材料」，
    不要求解析出正文。

    核定预算：报告的结论是钱，光写一句「通过」落不到批复上。这里记一个
    金额，默认带出可研投资，经办人按报告改；财政审核还能再改一次。
    """
    _require_doc(project, 'cost_review_task', '《费用评审任务书》')
    # 报告必须属于本轮（审查项 #5）：费用评审允许多轮，征求反馈后单位
    # 只补了证据、没出新报告时，不能拿上一轮的旧报告当本轮结论 —— 那等于
    # 审核岗替财评机构把旧意见又说了一遍。材料上传时已落 round。
    reports = list(project.files
                   .filter(doc_type=ProjectFile.DocType.COST_REPORT,
                           round=project.round)
                   .order_by('-id')
                   .values_list('filename', flat=True)[:5])
    if not reports:
        raise FlowError('本轮还没有上传第三方《费用评估报告》。请先在页面顶部「本环节办理」'
                        '的「第三方评估报告」里上传本轮报告，再点通过；'
                        '上一轮报告不能作为本轮结论依据')
    amount = _money(data, 'audit_amount', '评估核定投资')
    note = f'第三方《费用评估报告》已出具（{reports[0]}），评审结论：通过'
    if amount is not None:
        note += f'；核定投资 {amount:g} 万元'
    if len(reports) > 1:
        note += f'；本轮共 {len(reports)} 份，其余：{"；".join(reports[1:])}'
    project.audit_result = Project.AuditResult.PASS
    project.audit_note = note
    project.audited_at = timezone.now()
    fields = ['audit_result', 'audit_note', 'audited_at', 'updated']
    if amount is not None:
        project.budget_amount = amount
        fields.append('budget_amount')
    project.save(update_fields=fields)
    return ({'audit': 'pass', 'reports': reports,
             'budget_amount': float(project.budget_amount)
             if project.budget_amount is not None else None},
            {'comment': note})


def _h_cost_review_return(project, user, data):
    items = _items(data, 'items', '需要单位反馈的事项')
    note = _text(data, 'note', '费用评估意见', min_len=10, required=True)
    deadline = _date(data, 'deadline', '反馈期限')
    project.round += 1
    project.save(update_fields=['round', 'updated'])
    SupplementNotice.objects.create(
        project=project, round=project.round,
        kind=SupplementNotice.Kind.AUDIT, items=items, note=note,
        deadline=deadline, issued_by=user, status=SupplementNotice.Status.OPEN,
    )
    return ({'items': items, 'round': project.round}, {'comment': note})


def _h_cost_resubmit(project, user, data):
    """征求反馈后，申报单位反馈（补材料/提异议/直接确认），再回费用评审重评。

    和预审补正的区别：这里不重跑预审、不重开评审会，只把材料补回费用评审。

    这个动作允许「什么都不给」直接提交：单位核对了反馈事项、认定评估结论
    没问题也不需要补材料时，本来就没什么可填的，强填十个字只会逼出一句废话，
    留痕价值反而更低。空提交按「单位对评估结论无异议，无需补充材料」留痕，
    审核岗打开就知道这轮没有新增内容，该维持维持、该再退再退，不靠单位替
    审核岗做判断。

    反馈说明本身不设必填：对评估结论没异议、只按反馈事项补材料是常态，
    这种情形没有「异议理由」可写，留空时按
    「无异议，已按反馈事项补充材料」留痕。
    """
    note = _text(data, 'note', '反馈说明', min_len=10)
    round_files = project.files.filter(round=project.round)

    open_notices = project.notices.filter(status=SupplementNotice.Status.OPEN,
                                          kind=SupplementNotice.Kind.AUDIT)
    resolved = open_notices.count()
    evidence = list(round_files.values_list('filename', flat=True)[:8])
    if note:
        reply_text = note
    elif evidence:
        reply_text = '单位对评估结论无异议，已按反馈事项补充材料'
    else:
        reply_text = ('单位对评估结论无异议，无需补充材料'
                      '（本轮未上传材料、未填写说明）')
    if evidence:
        reply_text = f'{reply_text}\n随附材料：{"；".join(evidence)}'
    open_notices.update(status=SupplementNotice.Status.RESOLVED,
                        reply=reply_text, resolved_at=timezone.now())
    return ({'resolved_notices': resolved, 'files': len(evidence),
             'round': project.round},
            {'comment': reply_text})


def _h_cost_review_reject(project, user, data):
    note = _text(data, 'note', '不通过理由', min_len=10, required=True)
    project.audit_result = Project.AuditResult.REJECT
    project.audit_note = note
    project.audited_at = timezone.now()
    project.save(update_fields=['audit_result', 'audit_note', 'audited_at', 'updated'])
    return ({'audit': 'reject'}, {'comment': note})


def _h_fiscal_pass(project, user, data):
    note = _text(data, 'note', '财政审核意见', min_len=10, required=True)
    # 财政定的数以财政为准：预算已按财评结论填过一次，财政局核减/核增时
    # 在这里改；不改就是沿用财评结论，不必重复抄一遍数字
    amount = _money(data, 'budget_amount', '财政核定投资')
    project.fiscal_result = Project.FiscalResult.PASS
    project.fiscal_note = note
    project.fiscal_at = timezone.now()
    fields = ['fiscal_result', 'fiscal_note', 'fiscal_at', 'updated']
    if amount is not None:
        project.budget_amount = amount
        fields.append('budget_amount')
    project.save(update_fields=fields)
    # 资金来源不再让经办人选：按本地《数字化项目管理办法》，
    # 进了审批的都是市本级财政预算安排，选了反而多一处填错的地方。
    # 留痕里仍记一条固定值，年度统计与导出不用回头改口径
    return ({'fiscal': 'pass', 'fund_source': FISCAL_FUND_SOURCE,
             'budget_amount': float(project.budget_amount)
             if project.budget_amount is not None else None},
            {'comment': note})


def _h_fiscal_reject(project, user, data):
    note = _text(data, 'note', '财政审核意见', min_len=10, required=True)
    project.fiscal_result = Project.FiscalResult.REJECT
    project.fiscal_note = note
    project.fiscal_at = timezone.now()
    project.save(update_fields=['fiscal_result', 'fiscal_note', 'fiscal_at', 'updated'])
    return ({'fiscal': 'reject'}, {'comment': note})


def _h_approval_issue(project, user, data):
    note = _text(data, 'note', '立项说明', min_len=10, required=True)
    _require_doc(project, 'approval_reply', '《立项批复》')
    # 批复不再收文号（2026-11-24）：模板里已去掉「（克数发〔20XX〕X 号）」，
    # 表单里也不再问。仍收这个参数只为兼容老调用；没传就不动库里的旧值，
    # 免得把历史件的文号抹成空
    no = _text(data, 'approval_no', '批复文号')
    project.approval_note = note
    project.approval_at = timezone.now()
    fields = ['approval_note', 'approval_at', 'updated']
    if no:
        project.approval_no = no
        fields.append('approval_no')
    project.save(update_fields=fields)
    return ({'approval': 'issued'}, {'comment': note})


def _h_reserve_reactivate(project, user, data):
    note = _text(data, 'note', '重新纳入理由', min_len=10, required=True)
    # 重新纳入时清掉上一轮「无资金安排」的结论与理由：只清结论、留着旧
    # 否定意见，归档包里会出现「结论空、意见是旧否定理由」的怪记录。
    project.fiscal_result = ''
    project.fiscal_note = ''
    project.fiscal_at = None
    project.save(update_fields=['fiscal_result', 'fiscal_note', 'fiscal_at', 'updated'])
    return ({'reactivated': True}, {'comment': note})


def _h_start_build(project, user, data):
    """招标完成转建设。

    只记一句「招标已完成」：中标通知书、合同都是建设单位和招标代理之间的
    民事文件，本系统是审批管理台账，不替其他单位承担这些档案责任。
    """
    note = _text(data, 'note', '说明')
    return ({}, {'comment': note or '招标完成，转入建设中'})


def _h_start_trial(project, user, data):
    note = _text(data, 'note', '说明')
    return ({}, {'comment': note or '建设完成，转入试运行'})


def _h_apply_acceptance(project, user, data):
    """试运行情况 + 归集情况由申报单位填报，落到项目上。

    归集这段是《数据归集证明》的正文来源，所以必填且有最短字数 ——
    一句「已按要求归集」写不出证明，也挡不住事后追问。
    """
    trial = _text(data, 'trial_summary', '试运行情况', min_len=15, required=True)
    scope = _text(data, 'data_collect_scope', '数据归集情况', min_len=10, required=True)
    note = _text(data, 'note', '验收申请说明')
    project.trial_summary = trial
    project.data_collect_scope = scope
    project.acceptance_applied_at = timezone.now()
    project.save(update_fields=['trial_summary', 'data_collect_scope',
                               'acceptance_applied_at', 'updated'])
    comment = note or '试运行结束，申请验收'
    return ({}, {'comment': comment})


def _h_accept(project, user, data):
    """验收确认。

    一道硬门槛：《数据归集证明》已按模板出具 —— 由我局出、发给建设单位。
    建了系统却把数据留在自己机房，是我市数字化项目最普遍的问题，只有卡在
    验收这一关才拦得住。
    《专家验收意见》不卡在这一步：验收会开完才有那份签字件，属于结项后的
    备案件，结项后在「已验收」里上传即可。
    """
    # 证明必须存在（原硬门槛），且必须对应当前的归集口径（审查项 #6）：accept_return 退回后单位
    # 可能重填了归集说明，旧证明不能继续当这一版的门槛。meta.scope_hash
    # 是出具时正文的指纹；没有指纹的历史证明按旧文本比对，尽量不误伤。
    import hashlib
    scope = project.data_collect_scope or ''
    current_hash = hashlib.sha256(scope.encode('utf-8')).hexdigest()[:16]
    docs = list(project.documents.filter(kind='data_collect'))
    if not docs:
        raise FlowError('还没有出具《数据归集证明》。请先在「待验收」里按模板生成并'
                        '送达建设单位；数据未归集的项目不能验收。')
    valid = False
    for d in docs:
        meta = d.meta or {}
        h = meta.get('scope_hash')
        if (h == current_hash
                or (not h and (meta.get('data_collect') or '') == scope)):
            valid = True
            break
    if not valid:
        raise FlowError('《数据归集证明》与当前归集说明不一致 —— 可能是在退回补充后'
                        '重填了归集情况。请先在「待验收」按现在的口径重新出具证明，再验收。')
    note = _text(data, 'note', '验收情况', min_len=10)
    return ({'accepted': True}, {'comment': note or '验收合格，项目结项'})


def _h_accept_return(project, user, data):
    """退回补充：试运行或归集说明写不清，证明就出不来，退回重填。"""
    note = _text(data, 'note', '退回理由', min_len=10)
    return ({}, {'comment': note})


_HANDLERS = {
    'submit': _h_submit,
    'withdraw': _h_withdraw,
    'rule_check': _h_rule_check,
    'ai_review': _h_ai_review,
    'precheck_pass': _h_precheck_pass,
    'precheck_return': _h_precheck_return,
    'resubmit': _h_resubmit,
    'review_resubmit': _h_review_resubmit,
    'conclude': _h_conclude,
    'cost_review_pass': _h_cost_review_pass,
    'cost_review_return': _h_cost_review_return,
    'cost_resubmit': _h_cost_resubmit,
    'cost_review_reject': _h_cost_review_reject,
    'fiscal_pass': _h_fiscal_pass,
    'fiscal_reject': _h_fiscal_reject,
    'approval_issue': _h_approval_issue,
    'reserve_reactivate': _h_reserve_reactivate,
    'start_build': _h_start_build,
    'start_trial': _h_start_trial,
    'apply_acceptance': _h_apply_acceptance,
    'accept': _h_accept,
    'accept_return': _h_accept_return,
}


# ------------------------------------------------------------------ 待办
def stage_progress(stage):
    """阶段在整条线上的位置，前端画进度条用。"""
    order = [Project.Stage.DRAFT, Project.Stage.PRECHECK, Project.Stage.SUPPLEMENT,
             Project.Stage.EXPERT_REVIEW, Project.Stage.REVIEW_SUPPLEMENT,
             Project.Stage.COST_REVIEW, Project.Stage.COST_SUPPLEMENT,
             Project.Stage.FISCAL_REVIEW,
             Project.Stage.APPROVAL, Project.Stage.TENDERING,
             Project.Stage.BUILDING, Project.Stage.TRIAL, Project.Stage.ACCEPTANCE,
             Project.Stage.ACCEPTED]
    if stage == Project.Stage.REJECTED:
        return {'index': -1, 'total': len(order), 'terminal': 'rejected'}
    if stage == Project.Stage.RESERVE:
        # 储备库不在主线上，用终止样式单独标出来
        return {'index': -1, 'total': len(order), 'terminal': 'reserve'}
    try:
        return {'index': order.index(stage), 'total': len(order), 'terminal': ''}
    except ValueError:
        return {'index': 0, 'total': len(order), 'terminal': ''}


# ------------------------------------------------------------------ 环节时限
# 管理员没配的环节按这套兜底。数字是「自然日」，按管理惯例取整不取工作日：
# 系统里没有节假日日历，写「工作日」反而算不准
SLA_DEFAULTS = {
    Project.Stage.DRAFT: 7,
    Project.Stage.PRECHECK: 5,
    Project.Stage.SUPPLEMENT: 10,
    Project.Stage.EXPERT_REVIEW: 15,
    Project.Stage.REVIEW_SUPPLEMENT: 10,
    Project.Stage.COST_REVIEW: 10,
    Project.Stage.COST_SUPPLEMENT: 7,
    Project.Stage.FISCAL_REVIEW: 10,
    Project.Stage.APPROVAL: 5,
    Project.Stage.TENDERING: 30,
    Project.Stage.BUILDING: 180,
    Project.Stage.TRIAL: 90,
    Project.Stage.ACCEPTANCE: 30,
}

# 时限只对「有人在办」的环节有意义：已验收、不予通过、储备库不再计时
SLA_STAGES = list(SLA_DEFAULTS.keys())


def sla_days(stage):
    """取某环节的时限。表里有就用表里的，没有回落到默认值。"""
    from .models import StageSla

    if stage not in SLA_DEFAULTS:
        return 0
    rec = StageSla.objects.filter(stage=stage).only('days').first()
    if rec and rec.days and rec.days > 0:
        return rec.days
    return SLA_DEFAULTS.get(stage, 0)


def sla_state(project):
    """本环节时限执行情况。

    返回 days(时限)、used(已用自然日)、remain(剩余)、overdue(超期天数)。
    已结束或阶段不在计时范围内的，days=0，前端就不显示这块。
    used 与 days_in_stage 同一口径（都是已经过去的整天数），
    两个数并排显示时不会一个 1 天、一个 2 天，看着像系统算错了。
    """
    days = sla_days(project.stage)
    if not days or not project.stage_at:
        return {'days': 0, 'used': 0, 'remain': 0, 'overdue': 0}
    used = max((timezone.now() - project.stage_at).days, 0)
    return {
        'days': days,
        'used': used,
        'remain': max(days - used, 0),
        'overdue': max(used - days, 0),
    }
