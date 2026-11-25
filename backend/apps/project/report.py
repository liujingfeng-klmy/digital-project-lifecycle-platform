"""统计与归档：把流转记录算成用得上的数字。

口径说明（这几个数最容易算错，写清楚免得后面自己都看不懂）：
- 阶段耗时：按 ProjectEvent 里 to_stage 的变化切段，同一阶段停留多次则累加，
  分母是「走过这个阶段的项目数」，不是项目总数。
- 进行中阶段单独出「已停留」，不和已完成阶段的均值混在一起 —— 混在一起会把
  平均值拉向 0，看起来像效率很高，实际是还没走完。
- 退回率：进过「退回补正」的项目数 / 提交过的项目数。退回一次就算，不重复计数。
- 评审通过率：分母是已出评审结论的项目，未出结论的不算分母。
"""
import csv
import io
from collections import defaultdict

from django.db.models import Count, Sum
from django.utils import timezone

from apps.core.models import User

from .models import PrecheckReport, Project, ProjectEvent

# 走到这些阶段就算流程结束，不再累计「已停留」
TERMINAL = {Project.Stage.ACCEPTED, Project.Stage.REJECTED,
            Project.Stage.RESERVE}


def _stage_meta():
    return [{'key': k, 'label': v} for k, v in Project.Stage.choices]


def _days(seconds):
    return round(seconds / 86400, 1)


def _events(qs):
    """一次取全，别在循环里查库。"""
    return ProjectEvent.objects.filter(project__in=qs).order_by(
        'project_id', 'created', 'id').values(
        'project_id', 'action', 'from_stage', 'to_stage', 'created')


def build(qs):
    now = timezone.now()
    projects = list(qs.values('id', 'stage', 'unit', 'category', 'year',
                              'investment', 'review_result', 'created',
                              'submitted_at'))
    events = list(_events(qs))

    # ---------------------------------------------------------- 阶段耗时
    spent = defaultdict(float)      # stage -> 累计秒数
    visits = defaultdict(int)       # stage -> 进入次数（多轮补正会多次）
    ongoing = defaultdict(list)     # stage -> 当前正停在该阶段的项目已停留秒数
    seen_stage = defaultdict(set)   # stage -> 进过的项目 id

    by_project = defaultdict(list)
    for e in events:
        by_project[e['project_id']].append(e)

    for p in projects:
        evs = by_project.get(p['id'], [])
        if not evs:
            continue
        cur_stage, entered = None, None
        for e in evs:
            if e['to_stage'] and e['to_stage'] != cur_stage:
                if cur_stage and entered:
                    spent[cur_stage] += (e['created'] - entered).total_seconds()
                    visits[cur_stage] += 1
                cur_stage = e['to_stage']
                entered = e['created']
                seen_stage[cur_stage].add(p['id'])
        if cur_stage and entered:
            delta = (now - entered).total_seconds()
            if cur_stage in TERMINAL:
                spent[cur_stage] += delta
                visits[cur_stage] += 1
            else:
                ongoing[cur_stage].append(delta)

    stage_days = []
    for m in _stage_meta():
        k = m['key']
        # 平均耗时 = 该阶段所有停留时长 / 进入次数（同项目多轮各算一次，耗时才准）；
        # 走过该阶段的项目数单独用集合算，不能拿进入次数充数
        avg = _days(spent[k] / visits[k]) if visits[k] else None
        og = ongoing.get(k) or []
        stage_days.append({
            'key': k, 'label': m['label'],
            'avg_days': avg,
            'finished': len(seen_stage[k]),
            'visits': visits[k],
            'stuck': len(og),
            'stuck_avg_days': _days(sum(og) / len(og)) if og else None,
            'stuck_max_days': _days(max(og)) if og else None,
        })

    # ---------------------------------------------------------- 漏斗与退回
    funnel = [{'key': m['key'], 'label': m['label'], 'count': len(seen_stage[m['key']])}
              for m in _stage_meta()]

    # 退回率的分母是「提交过的项目」：既看 submitted_at，也看有没有 submit 事件，
    # 两个都算上才不会把历史数据漏掉。原来直接用 queryset 全量，
    # 草稿越多退回率越好看
    submitted = {p['id'] for p in projects if p['submitted_at']}
    submitted |= {e['project_id'] for e in events if e['action'] == 'submit'}
    # 「征求反馈」是费用评审多轮里的正常环节，不是材料被否的退回补正，
    # 不进退回率分子（审查项 #13）；另单列一项供管理上看多轮情况。
    returned_ids = {e['project_id'] for e in events
                    if e['action'] == 'precheck_return'}
    feedback_ids = {e['project_id'] for e in events
                    if e['action'] == 'cost_review_return'}
    return_rate = (len(returned_ids) / len(submitted)) if submitted else 0

    # ---------------------------------------------------------- 评审结论
    review = defaultdict(int)
    for p in projects:
        review[p['review_result'] or 'pending'] += 1
    concluded = sum(v for k, v in review.items() if k != 'pending')
    passed_n = review.get('pass', 0) + review.get('modified_pass', 0)
    review_rate = (passed_n / concluded) if concluded else 0

    # ---------------------------------------------------------- 分单位 / 分领域
    def group(key):
        agg = defaultdict(lambda: {'total': 0, 'investment': 0.0, 'accepted': 0})
        for p in projects:
            g = agg[p[key] or '未填']
            g['total'] += 1
            g['investment'] += float(p['investment'] or 0)
            if p['stage'] == Project.Stage.ACCEPTED:
                g['accepted'] += 1
        rows = [{'name': k, **v} for k, v in agg.items()]
        rows.sort(key=lambda r: -r['investment'])
        return rows

    # ---------------------------------------------------------- 预审发现量
    reports = (PrecheckReport.objects.filter(project__in=qs)
               .values('level').annotate(n=Count('id')))
    report_counts = {r['level']: r['n'] for r in reports}

    total_inv = float(qs.aggregate(s=Sum('investment'))['s'] or 0)
    accepted_days = []
    for p in projects:
        if p['stage'] != Project.Stage.ACCEPTED:
            continue
        evs = by_project.get(p['id'], [])
        if len(evs) >= 2:
            accepted_days.append((evs[-1]['created'] - evs[0]['created']).total_seconds())

    return {
        'summary': {
            'total': len(projects),
            'in_progress': sum(1 for p in projects
                               if p['stage'] not in TERMINAL),
            'accepted': sum(1 for p in projects
                            if p['stage'] == Project.Stage.ACCEPTED),
            'rejected': sum(1 for p in projects
                            if p['stage'] == Project.Stage.REJECTED),
            'investment_total': total_inv,
            'avg_cycle_days': (_days(sum(accepted_days) / len(accepted_days))
                               if accepted_days else None),
            'cycle_samples': len(accepted_days),
        },
        'stage_days': stage_days,
        'funnel': funnel,
        'return_rate': {
            'submitted': len(submitted),
            'returned': len(returned_ids),
            'rate': round(return_rate, 4),
            'feedback_projects': len(feedback_ids),
        },
        'review': {
            'pass': review.get('pass', 0),
            'modified_pass': review.get('modified_pass', 0),
            'reject': review.get('reject', 0),
            'pending': review.get('pending', 0),
            'concluded': concluded,
            'rate': round(review_rate, 4),
        },
        'by_unit': group('unit'),
        'by_category': group('category'),
        'by_year': sorted(
            ({'name': str(y), **v} for y, v in
             _year_group(projects).items()), key=lambda r: r['name']),
        'reports': report_counts,
        # 必须转本地时区：timezone.now() 是 UTC，直接 strftime 会差 8 小时
        'generated_at': timezone.localtime(now).strftime('%Y-%m-%d %H:%M'),
    }


def _year_group(projects):
    agg = defaultdict(lambda: {'total': 0, 'investment': 0.0, 'accepted': 0})
    for p in projects:
        g = agg[p['year'] or 0]
        g['total'] += 1
        g['investment'] += float(p['investment'] or 0)
        if p['stage'] == Project.Stage.ACCEPTED:
            g['accepted'] += 1
    return dict(agg)


# ------------------------------------------------------------------ 导出
def csv_response_rows(qs, kind):
    """导出用的表格行。加 BOM，Excel 双击打开不乱码。"""
    if kind == 'events':
        head = ['项目编号', '项目名称', '动作', '原阶段', '新阶段',
                '操作人', '角色', '意见', '时间']
        stage_label = dict(Project.Stage.choices)
        rows = []
        for e in (ProjectEvent.objects.filter(project__in=qs)
                  .select_related('project', 'actor').order_by('created', 'id')):
            rows.append([
                e.project.code, e.project.name, e.action_label,
                stage_label.get(e.from_stage, e.from_stage or ''),
                stage_label.get(e.to_stage, e.to_stage or ''),
                e.actor_name, e.actor_role_label,
                (e.comment or '').replace('\n', ' '),
                timezone.localtime(e.created).strftime('%Y-%m-%d %H:%M'),
            ])
        return head, rows

    head = ['项目编号', '项目名称', '申报单位', '申报人', '年度', '领域',
            '总投资(万元)', '建设周期(月)', '当前阶段', '当前办理',
            '评审结论', '审核结论', '查重疑点', '提交时间', '创建时间']
    stage_label = dict(Project.Stage.choices)
    role_label = dict(User.Role.choices)
    rows = []
    for p in qs.select_related('applicant').order_by('-created'):
        dup = (p.dup_check or {}).get('attention') or 0
        rows.append([
            p.code, p.name, p.unit,
            getattr(p.applicant, 'display', ''),
            p.year, p.category or '',
            float(p.investment or 0), p.duration_months or '',
            stage_label.get(p.stage, p.stage),
            role_label.get(p.handler_role, p.handler_role or ''),
            p.get_review_result_display() if p.review_result else '',
            p.get_audit_result_display() if p.audit_result else '',
            dup,
            timezone.localtime(p.submitted_at).strftime('%Y-%m-%d %H:%M')
            if p.submitted_at else '',
            timezone.localtime(p.created).strftime('%Y-%m-%d %H:%M'),
        ])
    return head, rows


def _csv_safe(value):
    """防止 CSV 公式注入。

    项目名称、单位、经办意见都是用户可控文本，若以 = + - @ 或制表/回车开头，
    用 Excel/WPS 打开导出表时会被当成公式执行。前置单引号让单元格按文本处理。
    """
    s = '' if value is None else str(value)
    if s[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + s
    return s


def to_csv(head, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_csv_safe(v) for v in head])
    for row in rows:
        w.writerow([_csv_safe(v) for v in row])
    return '\ufeff' + buf.getvalue()


def parse_range(request):
    """统一解析 year / unit / category 三个筛选，列表页与统计页共用。"""
    out = {}
    year = request.query_params.get('year')
    if year and str(year).isdigit():
        out['year'] = int(year)
    unit = (request.query_params.get('unit') or '').strip()
    if unit:
        out['unit'] = unit
    cat = (request.query_params.get('category') or '').strip()
    if cat:
        out['category'] = cat
    return out
