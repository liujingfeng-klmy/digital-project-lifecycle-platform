"""按模板生成对外文书。

三份文书都从 docx 模板来，模板里用 {{project_name}} 这种占位符标出可替换字段：

    预审修正说明   precheck_notice    —— 初审之后，按勾选的预审/AI 问题生成
    费用评审任务书 cost_review_task   —— 交财评机构，评审期限默认 7 个自然日
    立项批复       approval_reply     —— 财政审核同意后，给申报单位的批复
    数据归集证明   data_collect       —— 待验收时由我局出具，发给建设单位

管理员在「模板库」里上传新版本后自动改用上传件；没上传时用后端打包的模板。
"""
import copy
import io
import re
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from docx import Document

from .models import DocTemplate, ProjectDocument

PACKAGED = Path(__file__).resolve().parent / 'docs' / 'templates'
_CN = '一二三四五六七八九十'
_ITEM_RE = re.compile(r'^([一二三四五六七八九十]+)、')

# 立项批复的两项固定口径：资金来源与建设地点在本市不存在第二种情形
FUND_SOURCE = '市本级财政预算'
# 建设地点与出具单位属本地化信息，从 config.json 的 org 段读（见 settings.py），
# 未配置时用中性占位值。源码里不写具体城市与单位名。
SITE = getattr(settings, 'ORG_SITE', '示例市')
# 《数据归集证明》的出具单位，模板里 {{issuer}} 用
ISSUER = getattr(settings, 'ORG_ISSUER', '示例市数据管理局')
# 文书正文引用的本地办法名，模板里 {{policy_name}} 用
POLICY_NAME = getattr(settings, 'ORG_POLICY_NAME', '数字化项目管理办法')


# ------------------------------------------------------------------ 小工具
def _cn_num(n):
    if 1 <= n <= 10:
        return _CN[n - 1]
    if 11 <= n <= 19:
        return '十' + _CN[n - 11]
    if n % 10 == 0:
        return _CN[n // 10 - 1] + '十'
    return _CN[n // 10 - 1] + '十' + _CN[n % 10 - 1]


def cn_date(d):
    """公文日期：2026年11月20日。"""
    return f'{d.year}年{d.month}月{d.day}日'


def _num(v):
    if v in (None, ''):
        return ''
    try:
        return f'{float(v):g}'
    except (TypeError, ValueError):
        return str(v)


def _apply(text, ctx):
    # 一次性正则替换：原来按 ctx 顺序多轮 str.replace，若项目名里含
    # {{unit}} 这样的占位符文本，先被 project_name 注入、再被 unit 那轮
    # 二次展开，生成的文书里项目名会变成单位名。单次扫描只认原文占位符，
    # 替换进来的值不再被解释。未定义的占位符原样保留，便于发现漏填字段。
    def _sub(m):
        k = m.group(1)
        if k not in ctx:            # 未定义的占位符原样保留，便于发现漏填字段
            return m.group(0)
        v = ctx[k]
        return '' if v is None else str(v)

    return re.sub(r'\{\{(\w+)\}\}', _sub, text)


def _replace_paragraph(p, ctx):
    old = ''.join(r.text for r in p.runs)
    if '{{' not in old:
        return
    new = _apply(old, ctx)
    if new == old:
        return
    if not p.runs:
        p.add_run(new)
        return
    # 统一写进第一个 run：占位符通常单独成段，混排格式在公文模板里不存在
    p.runs[0].text = new
    for r in p.runs[1:]:
        r._element.getparent().remove(r._element)


def _replace_all(doc, ctx):
    for p in doc.paragraphs:
        _replace_paragraph(p, ctx)
    for table in doc.tables:
        for row in table.rows:
            seen = set()
            for cell in row.cells:
                # 合并单元格会返回同一个 tc，重复处理会清掉刚写进去的换行
                if id(cell._tc) in seen:
                    continue
                seen.add(id(cell._tc))
                for p in cell.paragraphs:
                    _replace_paragraph(p, ctx)


def _set_text(p, text):
    """整段改写，保留首 run 的字体，去掉填空下划线。"""
    if p.runs:
        p.runs[0].text = text
        p.runs[0].font.underline = False
        for r in p.runs[1:]:
            r._element.getparent().remove(r._element)
    else:
        p.add_run(text)


def _item_text(it):
    title = (it.get('title') or '').strip().rstrip('。')
    advice = (it.get('advice') or '').strip()
    detail = (it.get('detail') or '').strip()
    if advice:
        return f'{title}。{advice}'
    if detail and len(detail) <= 90:
        return f'{title}。{detail}'
    return title


def _fill_items(doc, items):
    """把选中的问题填进「一、二、三…」填空行。

    条数少于模板行数就删掉多余空行，多于行数就复制最后一行再续号 ——
    初审人员勾几条，函里就出几条，不留空行也不截断。
    """
    lines = [p for p in doc.paragraphs if _ITEM_RE.match(p.text.strip())]
    if not lines:
        return
    texts = [_item_text(it) for it in items] or ['（未选择问题项）']
    while len(lines) < len(texts):
        last = lines[-1]
        new_p = copy.deepcopy(last._p)
        last._p.addnext(new_p)
        lines = [p for p in doc.paragraphs if _ITEM_RE.match(p.text.strip())]
    for i, p in enumerate(lines[:len(texts)]):
        _set_text(p, f'{_cn_num(i + 1)}、{texts[i]}')
    for p in lines[len(texts):]:
        p._element.getparent().remove(p._element)


def _template_for(kind, stage=None):
    """取当前启用的模板：按「环节 + 文书类型」精确匹配，未命中再回退通用。

    用户拍板 #20：界面上的「适用环节」要真正生效。同一 kind 建了两份不同
    环节的模板时，各自只在本环节使用；没有该环节模板时，仍用该 kind 最新
    的一份（stage 为空的通用模板，或历史单份模板），保证升级平滑。
    """
    qs = DocTemplate.objects.filter(kind=kind, active=True)
    tpl = None
    if stage:
        tpl = qs.filter(stage=stage).order_by('-updated').first()
    if tpl is None:
        tpl = qs.order_by('-updated').first()
    if tpl and tpl.file:
        try:
            with tpl.file.open('rb') as fh:
                return Document(fh), tpl
        except (FileNotFoundError, OSError):
            pass  # 文件被移走时退回打包模板，不能让生成直接失败
    key = (tpl.builtin_key if tpl and tpl.builtin_key else kind)
    path = PACKAGED / f'{key}.docx'
    if not path.exists():
        raise FileNotFoundError(f'模板 {key}.docx 不存在，请管理员在模板库上传')
    return Document(path), tpl


def _base_ctx(project, today=None):
    today = today or date.today()
    return {
        'project_name': project.name,
        'project_code': project.code,
        'unit': project.unit,
        'year': project.year or '',
        'investment': _num(project.investment_wan),
        # 批复里的总投资：财评/财政核定数优先，没有才回落到可研申报数
        'budget': _num(project.budget_wan),
        'duration_months': project.duration_months or '',
        'date': cn_date(today),
        # 落款单位与建设地点：四份文书都要用，统一在这里给，
        # 取值来自 config.json 的 org 段（见 settings.py），模板里写 {{issuer}} / {{site}}
        'issuer': ISSUER,
        'site': SITE,
        'policy_name': POLICY_NAME,
    }


def _save(project, kind, name, doc, tpl, user, meta):
    buf = io.BytesIO()
    doc.save(buf)
    rec = ProjectDocument(
        project=project, kind=kind, name=name, stage=project.stage,
        round=project.round, template=tpl, meta=meta, created_by=user)
    rec.file.save(name, ContentFile(buf.getvalue()), save=False)
    rec.save()
    return rec


# ------------------------------------------------------------------ 预审候选问题
def precheck_candidates(project):
    """把本轮规则预审与 AI 审查的问题合成一张可勾选清单。

    只列 block / warn：pass 项不是问题，info 项是建议，不放进对外函件。
    key 用「来源:序号」，生成时按 key 回查原文，不信任前端传来的标题。
    """
    out = []
    rule = (project.reports.filter(level='rule', round=project.round)
            .order_by('-created').first())
    ai = (project.reports.filter(level='ai', round=project.round)
          .order_by('-created').first())
    if rule:
        for i, it in enumerate(rule.items or []):
            if it.get('severity') not in ('block', 'warn'):
                continue
            out.append({
                'key': f'rule:{i}', 'source': 'rule', 'source_label': '规则预审',
                'level': it.get('severity'), 'group': it.get('group', ''),
                'title': it.get('title', ''), 'detail': it.get('detail', ''),
                'advice': it.get('advice', ''),
            })
    if ai:
        for i, f in enumerate(ai.findings or []):
            if f.get('level') not in ('block', 'warn'):
                continue
            out.append({
                'key': f'ai:{i}', 'source': 'ai', 'source_label': 'AI 深度审查',
                'level': f.get('level'), 'group': f.get('section', ''),
                'title': f.get('title', ''), 'detail': f.get('issue', ''),
                'advice': f.get('advice', ''), 'quote': f.get('quote', ''),
            })
    return out


def pick_candidates(project, keys):
    pool = {c['key']: c for c in precheck_candidates(project)}
    return [pool[k] for k in keys if k in pool]


# ------------------------------------------------------------------ 三份文书
def generate_precheck_notice(project, items, deadline, user):
    doc, tpl = _template_for('precheck_notice', project.stage)
    ctx = _base_ctx(project)
    ctx['deadline'] = cn_date(deadline) if deadline else '____年__月__日'
    _replace_all(doc, ctx)
    _fill_items(doc, items)
    return _save(project, 'precheck_notice',
                 f'预审修正说明-{project.code}.docx', doc, tpl, user,
                 {'items': [i.get('title', '') for i in items],
                  'deadline': str(deadline or '')})


def generate_cost_task(project, data, user):
    doc, tpl = _template_for('cost_review_task', project.stage)
    today = date.today()
    try:
        days = int(data.get('days') or 7)
    except (TypeError, ValueError):
        days = 7
    deadline = today + timedelta(days=days)
    applicant = project.applicant
    ctx = _base_ctx(project, today)
    # 任务书要填两组联系人：委托单位（我局经办人）与建设单位（申报单位经办人）。
    # 表单留空时按「当前经办人 / 申报人」兜底，否则生成出来只有机构名、
    # 财评机构拿不到对接人，任务书等于没法用。
    ctx.update({
        'doc_no': (data.get('doc_no') or f'CW-{project.code}-{today:%Y%m%d}'),
        'entrust_unit': (data.get('entrust_unit') or '财评机构'),
        'entrust_contact': ((data.get('entrust_contact') or '').strip()
                            or getattr(user, 'display', '')),
        'entrust_phone': ((data.get('entrust_phone') or '').strip()
                          or getattr(user, 'phone', '')),
        'applicant_contact': ((data.get('unit_contact') or '').strip()
                              or (applicant.display if applicant else '')),
        'applicant_phone': ((data.get('unit_phone') or '').strip()
                            or (getattr(applicant, 'phone', '') if applicant else '')),
        'deadline': cn_date(deadline),
        'days': days,
    })
    _replace_all(doc, ctx)
    return _save(project, 'cost_review_task',
                 f'费用评审任务书-{project.code}.docx', doc, tpl, user,
                 {'entrust_unit': ctx['entrust_unit'], 'days': days,
                  'deadline': str(deadline),
                  'entrust_contact': ctx['entrust_contact'],
                  'entrust_phone': ctx['entrust_phone'],
                  'unit_contact': ctx['applicant_contact'],
                  'unit_phone': ctx['applicant_phone']})


def generate_approval(project, data, user):
    doc, tpl = _template_for('approval_reply', project.stage)
    ctx = _base_ctx(project)
    # 这两项按本地数字化项目的固定口径写死，不再让经办人每次填：
    # 进入审批的项目资金一律由市本级财政预算安排，建设地点一律在本市。
    # 打包模板里已直接写成固定值，这里再给一份占位符取值 —— 管理员上传的
    # 新版模板若用了 {{fund_source}} / {{site}}，同样能填上
    ctx.update({'fund_source': FUND_SOURCE, 'site': SITE})
    _replace_all(doc, ctx)
    return _save(project, 'approval_reply',
                 f'立项批复-{project.code}.docx', doc, tpl, user,
                 {'approval_no': data.get('approval_no') or '',
                  'budget': ctx['budget'],
                  'fund_source': FUND_SOURCE, 'site': SITE})


def generate_data_collect(project, data, user):
    """出具《数据归集证明》。

    正文里的「试运行情况」「数据归集情况」直接取建设单位在申请验收时
    填报的那两段 —— 证明不是我局替他们写，是把他们报的内容核过之后
    盖章认下来。参数可覆盖（审核岗发现表述不清时先改后出，不改库里的
    原始申报文本，改的是出具这一份的口径）。
    """
    doc, tpl = _template_for('data_collect', project.stage)
    scope = (data.get('data_collect_scope') or project.data_collect_scope or '').strip()
    trial = (data.get('trial_summary') or project.trial_summary or '').strip()
    if not scope:
        raise ValueError('还没有归集说明，出不了证明：请先由申报单位在申请验收时填写')
    ctx = _base_ctx(project)
    ctx.update({
        'doc_no': (data.get('doc_no') or '').strip() or f'SJH-{project.code}',
        'trial_summary': trial or '（建设单位未报送试运行情况）',
        'data_collect': scope,
        'issuer': ISSUER,
    })
    _replace_all(doc, ctx)
    import hashlib
    _scope_hash = hashlib.sha256(scope.encode('utf-8')).hexdigest()[:16]
    return _save(project, 'data_collect',
                 f'数据归集证明-{project.code}.docx', doc, tpl, user,
                 {'doc_no': ctx['doc_no'], 'issuer': ISSUER,
                  'data_collect': scope, 'scope_hash': _scope_hash})
