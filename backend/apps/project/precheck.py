"""预审引擎。

分两级，职责不同：

一级（规则预审）—— 只做机器能确证的事：材料齐不齐、要素缺不缺、投资数字对不对得上、
章节结构够不够、与历史项目有没有明显疑点。查不出的东西绝不猜，宁可写「未发现」。

二级（AI 深度审查）—— 逐章审内容：必要性论证是否站得住、需求与现状是否对应、
建设内容有没有把老系统换名重报、技术方案是否可行、投资与工作量是否匹配、
绩效目标能不能考核。产出的是「问题 + 原文 + 建议」，不是结论。

两级都不出审批结论。预审结论由初审岗出，专家核定重复建设，审核岗审定立项。
"""
import json
import logging
import re
import time
from difflib import SequenceMatcher

from django.utils import timezone

from apps.archive import extract as archive_extract
from apps.archive import functions as archive_functions
from apps.archive import services as archive_services
from apps.archive import similarity as archive_similarity
from apps.archive import textparse
from apps.archive.models import ArchiveFile

from .models import PrecheckReport, Project, ProjectFile

log = logging.getLogger(__name__)

ENGINE_VERSION = '规则引擎 v1（结构 + 要素 + 勾稽 + 查重）'

# 可研必备章节，缺得太多说明材料是拼的
REQUIRED_CATS = [
    ('necessity', '建设必要性'),
    ('demand', '需求分析'),
    ('plan', '建设内容与方案'),
    ('tech', '技术路线'),
    ('invest', '投资估算'),
    ('perf', '绩效目标'),
]
OPTIONAL_CATS = [('security', '安全保障'), ('data', '数据与共享'),
                 ('risk', '风险分析'), ('schedule', '实施计划'),
                 ('ops', '运维保障')]

_PLACEHOLDERS = ('待补充', '待完善', 'TBD', 'XXX', 'xxx', '此处填写', '略')
_YUAN_PAT = re.compile(r'(?<!万)(?<!万元)\b\d{6,}(?:\.\d+)?\s*元')
_TOTAL_PAT = re.compile(r'(?:合\s*计|总\s*计|小\s*计)[^\d\n]{0,12}([\d,]+(?:\.\d+)?)\s*万元')


# ------------------------------------------------------------------ 基础工具
def _item(code, group, title, severity, detail, advice=''):
    return {'code': code, 'group': group, 'title': title, 'severity': severity,
            'detail': detail, 'advice': advice}


# 只有申报正文参与预审与查重（审查项 #10）：预算表 / 费用评估报告 / 异议证据 /
# 归集证明 / 验收意见都不是可研正文，混进来会造出假疑点、也会让旧正文继续
# 参与本轮判定。
CORPUS_TYPES = frozenset({
    ProjectFile.DocType.FEASIBILITY, ProjectFile.DocType.PROPOSAL,
    ProjectFile.DocType.APPROVAL, ProjectFile.DocType.OTHER,
})


def collect(project, *, corpus_only=False):
    """取当前版本的材料。

    解析失败的新版本不会顶掉旧版（parsing 会回退 is_current），所以按
    is_current 取就是「申报单位本轮实际要审的那一版」；旧轮次的正文不再
    混进本轮结论。corpus_only=True 时只留申报正文类，供查重与 AI 通读使用 ——
    预算表、费用评估报告、异议证据不是可研正文，进池只会造出假疑点。
    规则预审要核对投资概算，不能用这个开关。
    """
    qs = project.files.filter(status=ProjectFile.Status.READY, is_current=True)
    if corpus_only:
        qs = qs.filter(doc_type__in=CORPUS_TYPES)
    files = list(qs)
    text = '\n'.join((f.text or '') for f in files)
    sections = []
    for f in files:
        sections.extend(f.sections or [])
    return files, text, sections


def _pick_files(files, *types):
    return [f for f in files if f.doc_type in types]


def _ratio(a, b):
    if not a or not b:
        return 0.0
    return abs(a - b) / max(a, b)


# ------------------------------------------------------------------ 重复建设筛查
def screen_duplicate(project):
    """拿项目材料与历史档案比一遍，只留疑点，不出结论。

    返回结构会存进 Project.dup_check，页面直接渲染，不再重算 ——
    比对一次要一两秒，每次打开详情都算一遍既慢又浪费。
    """
    files, text, sections = collect(project, corpus_only=True)
    if not text:
        return {'version': 3, 'level': 'skip', 'level_label': '无可比对正文',
                'summary': '材料尚未解析出正文，无法查重', 'ranking': []}

    payload = {
        'text': text,
        'name': project.name,
        'unit': project.unit,
        'year': project.year,
        'investment': project.investment_wan,
    }
    try:
        q = archive_services.build_query(payload)

        # 同一份材料若已入库历史档案，要排除，否则会跟自己比出 100%
        hashes = {archive_similarity.text_fingerprint(f.text or '')
                  for f in files if f.text}
        hashes.add(q['hash'] if 'hash' in q else archive_similarity.text_fingerprint(text))
        self_ids = list(ArchiveFile.objects.filter(text_hash__in=list(hashes))
                        .values_list('id', flat=True))

        # 一定要带上片段：初审要的是「哪一段跟哪一段重复」，
        # 只给一个功能重合率，人还是得把两份材料从头翻到尾。
        res = archive_services.run_compare(q, top_n=3, include_snippets=True,
                                           snippet_top=6, exclude_file_ids=self_ids)
    except Exception as exc:  # 查重失败不能挡住预审，如实说明即可
        log.warning('重复建设筛查失败 %s：%s', project.code, exc)
        return {'version': 3, 'level': 'error', 'level_label': '查重未完成',
                'summary': f'查重过程出错：{exc}', 'ranking': []}

    ranking = []
    for r in res.get('ranking', []):
        cs = r['finding_counts']
        fn = r.get('functions') or {}
        ranking.append({
            'project_id': r.get('project_id'),
            'file_id': r.get('file_id'),
            'filename': r.get('filename', ''),
            'doc_type': r.get('doc_type', ''),
            'project_name': r.get('project_name'),
            'unit': r.get('unit'),
            'year': r.get('year'),
            'investment': r.get('investment'),
            'relevance': r['scores']['relevance'],
            'function_overlap': r['functions']['overlap'],
            'level': r['screen']['level'],
            'level_label': r['screen']['level_label'],
            'summary': r['screen']['summary'],
            'attention': cs.get('attention', 0),
            'verify': cs.get('verify', 0),
            'findings': [{'title': f['title'], 'level': f['level'],
                          'level_label': f.get('level_label', ''),
                          'detail': f['detail'], 'ask': f.get('ask', ''),
                          'evidence': (f.get('evidence') or [])[:8]}
                         for f in (r.get('findings') or [])][:6],
            'reasons': (r.get('reasons') or [])[:3],
            # —— 以下四块是「哪儿重复」的对照材料，缺了它们初审只能翻原文
            'functions': {
                'overlap': fn.get('overlap', 0),
                'business_q': fn.get('business_q', 0),
                'business_d': fn.get('business_d', 0),
                'pair_count': len(fn.get('pairs') or []),
                'pairs': [{'query': p['query'], 'hit': p['hit'], 'score': p['score'],
                           'query_section': p.get('query_section', ''),
                           'hit_section': p.get('hit_section', '')}
                          for p in fn.get('pairs', [])][:12],
                'generic_matched': fn.get('generic_matched', [])[:8],
            },
            'sections': [{'category': s['category'], 'label': s['label'],
                          'score': s['score'], 'weight': s.get('weight', 0),
                          'query_chars': s.get('query_chars', 0),
                          'hit_chars': s.get('hit_chars', 0)}
                         for s in (r.get('sections') or [])][:8],
            'snippets': [{'score': s['score'],
                          'query_section': s.get('query_section', ''),
                          'hit_section': s.get('hit_section', ''),
                          'query_text': s['query_text'],
                          'hit_text': s['hit_text']}
                         for s in (r.get('snippets') or [])][:6],
            'domain': r.get('domain') or {},
            'scores': r.get('scores') or {},
        })

    head = res.get('headline', {})
    scope = (res.get('query') or {}).get('scope') or {}
    return {
        'version': 3,  # 3 起只比建设内容章；1 只有摘要，2 是整篇比
        'level': head.get('level', 'none'),
        'level_label': head.get('level_label', ''),
        'summary': head.get('label', ''),
        'attention': head.get('attention', 0),
        'verify': head.get('verify', 0),
        'top_project': head.get('project', ''),
        'ranking': ranking,
        'scope': scope,
        'scanned': res.get('summary', {}).get('scanned_files', 0),
        'compared': res.get('summary', {}).get('compared', 0),
        'elapsed_ms': res.get('summary', {}).get('elapsed_ms', 0),
        'checked_at': timezone.localtime().strftime('%Y-%m-%d %H:%M'),
        'note': '只比建设内容（含需求分析）章，通用章节不参与。系统只列疑点，'
                '是否构成重复建设由人工比对两份材料后核定。',
    }


# ------------------------------------------------------------------ 一级：规则
def run_rule_check(project, user):
    files, text, sections = collect(project)
    items = []
    add = items.append

    # 1. 材料完整性 -------------------------------------------------
    ready_proposal = _pick_files(files, ProjectFile.DocType.FEASIBILITY,
                                 ProjectFile.DocType.PROPOSAL)
    budgets = _pick_files(files, ProjectFile.DocType.BUDGET)
    failed = list(project.files.filter(status=ProjectFile.Status.FAILED))

    if not files:
        add(_item('file_none', '材料完整性', '没有可用材料', 'block',
                  '上传的文件均未解析成功，或尚未上传。',
                  '上传可研报告（PDF 或 DOCX），有文字层的材料几秒内即可解析完成。'))
    elif not ready_proposal:
        add(_item('file_core', '材料完整性', '缺可研报告或项目建议书', 'block',
                  '现有材料中只有概算/批复/其他材料，缺立项依据主件。',
                  '补传可行性研究报告或项目建议书；只有概算无法判断建设内容与必要性。'))
    else:
        add(_item('file_core', '材料完整性', '立项主件齐全', 'pass',
                  '已具备：' + '、'.join(f.filename for f in ready_proposal[:3])))

    if not budgets:
        add(_item('file_budget', '材料完整性', '缺投资概算', 'warn',
                  '没有单独的投资概算文件，投资构成无法逐项核对。',
                  '补传概算表；若投资估算已含在可研内，请在可研投资章写明分项与合计。'))
    else:
        add(_item('file_budget', '材料完整性', '投资概算已上传', 'pass',
                  '、'.join(f.filename for f in budgets[:3])))

    if failed:
        add(_item('file_failed', '材料完整性', '有材料解析失败', 'warn',
                  '失败文件：' + '、'.join(f'{f.filename}（{(f.error or "")[:40]}）'
                                          for f in failed[:4]),
                  '扫描件需先 OCR；加密 PDF 需先解密；旧版 .doc 请另存为 .docx 后重传。'))

    # 2. 申报要素 ---------------------------------------------------
    problems = []
    if len((project.name or '').strip()) < 4:
        problems.append(('name', 'block', '项目名称少于 4 个字'))
    if not (project.unit or '').strip():
        problems.append(('unit', 'block', '未填写申报单位'))
    if not project.investment or project.investment <= 0:
        problems.append(('investment', 'block', '总投资未填或为 0'))
    if not project.year:
        problems.append(('year', 'warn', '未填写申报年度'))
    if not (project.category or '').strip():
        problems.append(('category', 'warn', '未填写业务领域'))
    if not project.duration_months:
        problems.append(('duration', 'warn', '未填写建设周期'))
    summary_len = len((project.summary or '').strip())
    if summary_len < 100:
        problems.append(('summary', 'warn' if summary_len >= 30 else 'block',
                         f'建设内容说明只有 {summary_len} 字'))
    if len((project.necessity or '').strip()) < 50:
        problems.append(('necessity', 'warn', '建设必要性未填写或过于简略'))
    if not (project.basis or '').strip():
        problems.append(('basis', 'warn', '未填写政策依据'))

    blocks = [p for p in problems if p[1] == 'block']
    warns = [p for p in problems if p[1] == 'warn']
    if blocks:
        add(_item('meta_block', '申报要素', '关键要素缺失', 'block',
                  '；'.join(p[2] for p in blocks),
                  '在项目基本信息里补齐后重新提交预审。'))
    if warns:
        add(_item('meta_warn', '申报要素', '次要要素待补充', 'warn',
                  '；'.join(p[2] for p in warns),
                  '这些字段不影响受理，但评审专家会看，建议补齐。'))
    if not problems:
        add(_item('meta_ok', '申报要素', '申报要素齐全', 'pass',
                  '名称、单位、投资、年度、领域、周期、建设内容、必要性、依据均已填写。'))

    # 材料里的项目名称与申报名称是否一致
    if text:
        meta = archive_extract.extract(text[:12000], ready_proposal[0].filename
                                       if ready_proposal else '')
        if meta.get('name'):
            sim = SequenceMatcher(None, _norm(meta['name']), _norm(project.name)).ratio()
            if sim < 0.55:
                add(_item('name_mismatch', '申报要素', '材料中的项目名称与申报名称不一致',
                          'warn',
                          f'材料识别为《{meta["name"]}》，申报名称为《{project.name}》，'
                          f'相似度 {sim:.0%}。',
                          '确认是否传错材料，或在材料封面统一名称后重传。'))
        if meta.get('unit') and project.unit:
            if _norm(meta['unit']) not in _norm(project.unit) \
                    and _norm(project.unit) not in _norm(meta['unit']):
                add(_item('unit_mismatch', '申报要素', '材料中的建设单位与申报单位不一致',
                          'warn',
                          f'材料识别为「{meta["unit"]}」，申报单位为「{project.unit}」。',
                          '如为代建或二级单位申报，请在补正说明中写明关系。'))

    # 3. 投资勾稽 ---------------------------------------------------
    invest_detail = _check_investment(project, text, budgets)
    items.extend(invest_detail)

    # 4. 章节结构 ---------------------------------------------------
    cats = {}
    for s in sections:
        c = s.get('category') or 'other'
        cats[c] = cats.get(c, 0) + len(s.get('text') or '')

    missing = [(k, label) for k, label in REQUIRED_CATS if k not in cats]
    extra_missing = [(k, label) for k, label in OPTIONAL_CATS if k not in cats]
    if not sections:
        add(_item('sec_none', '章节结构', '未能切出章节', 'block',
                  '正文没有可识别的章节标题。',
                  '检查材料是否为表格拼接或图片导出；建议用带标题样式的 Word 重新导出 PDF。'))
    elif len(missing) >= 3:
        add(_item('sec_missing', '章节结构', '必备章节缺失较多', 'block',
                  f'缺 {len(missing)} 个必备章节：'
                  + '、'.join(label for _, label in missing),
                  '可研报告需包含必要性、需求分析、建设内容与方案、技术路线、投资估算、绩效目标。'))
    elif missing:
        add(_item('sec_missing', '章节结构', '部分必备章节缺失', 'warn',
                  '缺：' + '、'.join(label for _, label in missing),
                  '补充对应章节；若内容已并入其他章节，请在标题中体现，便于评审定位。'))
    else:
        add(_item('sec_ok', '章节结构', '必备章节齐全', 'pass',
                  '必要性、需求分析、建设内容、技术路线、投资估算、绩效目标均能定位。'))

    if len(extra_missing) >= 3 and sections:
        add(_item('sec_extra', '章节结构', '辅助章节缺得较多', 'warn',
                  '缺：' + '、'.join(label for _, label in extra_missing),
                  '安全保障、数据共享、风险分析、运维保障是评审常问项，建议补齐。'))

    other_chars = cats.get('other', 0)
    total_chars = sum(cats.values()) or 1
    if sections and other_chars / total_chars > 0.6:
        add(_item('sec_style', '章节结构', '章节标题不规范', 'warn',
                  f'{other_chars / total_chars:.0%} 的正文归不到任何标准章节，'
                  '说明标题层级混乱或大量使用表格。',
                  '按「第X章 / 一、（一）/ 1.1」统一标题层级，避免整章无标题。'))

    # 5. 重复建设 ---------------------------------------------------
    dup = screen_duplicate(project)
    att, ver = dup.get('attention', 0), dup.get('verify', 0)
    if dup.get('level') in ('skip', 'error'):
        add(_item('dup_skip', '重复建设', '未完成查重', 'warn',
                  dup.get('summary', ''), '补齐正文后重新预审。'))
    elif att:
        add(_item('dup_attention', '重复建设', '与历史项目存在功能重合疑点', 'warn',
                  f'最相近的是《{dup.get("top_project")}》，{dup.get("summary")}。'
                  '疑点共 %d 项，详见下方查重结果。' % (att + ver),
                  '请逐条核对两份材料的建设内容章；疑点不等于重复，需专家核定。'))
    elif ver:
        add(_item('dup_verify', '重复建设', '存在待核实的功能重合', 'warn',
                  f'与《{dup.get("top_project")}》有 {ver} 项待核实疑点。',
                  '请专家确认这些功能是否属同一业务事项。'))
    else:
        add(_item('dup_ok', '重复建设', '未筛出重复建设疑点', 'pass',
                  f'已比对 {dup.get("compared", 0)} 个历史项目，未发现需要核实的重合。'
                  '注意：档案库不全时，查不出不等于不存在。'))

    # 6. 文本规范 ---------------------------------------------------
    total = sum(f.char_count or 0 for f in files)
    if files and total < 3000:
        add(_item('text_short', '文本规范', '正文过短', 'warn',
                  f'全部材料合计 {total} 字，不足以支撑可研论证。',
                  '补充现状分析、需求测算与方案比选。'))
    found_ph = [w for w in _PLACEHOLDERS if w in text]
    if found_ph:
        add(_item('text_placeholder', '文本规范', '存在未填写的占位内容', 'warn',
                  '正文中出现：' + '、'.join(found_ph[:5]),
                  '定稿前清理「待补充」「此处填写」等占位文字。'))
    if _YUAN_PAT.search(text):
        add(_item('text_unit', '文本规范', '金额单位可能是元', 'warn',
                  '正文出现 6 位以上不带「万」的金额，容易与万元混淆。',
                  '全文统一用万元，或在首次出现处标注单位。'))

    counts = {k: sum(1 for i in items if i['severity'] == k)
              for k in ('block', 'warn', 'pass')}
    counts['total'] = len(items)
    verdict = (PrecheckReport.Verdict.BLOCK if counts['block']
               else PrecheckReport.Verdict.ATTENTION if counts['warn']
               else PrecheckReport.Verdict.PASS)

    _drop_stale(project, PrecheckReport.Level.RULE)
    report = PrecheckReport.objects.create(
        project=project, round=project.round, level=PrecheckReport.Level.RULE,
        verdict=verdict, counts=counts, items=items,
        note=f'{ENGINE_VERSION}；检查 {counts["total"]} 项，'
             f'必须补正 {counts["block"]} 项、需说明 {counts["warn"]} 项。'
             '规则预审只覆盖可以确证的形式问题，内容质量由 AI 审查与专家评审负责。',
        created_by=user,
    )
    return report, dup


def _norm(s):
    return re.sub(r'[\s（）()《》【】\[\]、，,。.:：;；\-—_]', '', s or '')


def _check_investment(project, text, budgets):
    out = []
    total = project.investment_wan

    budget_invest = None
    for b in budgets:
        meta = archive_extract.extract((b.text or '')[:20000], b.filename)
        if meta.get('investment'):
            budget_invest = meta['investment']
            break

    if total and budget_invest:
        diff = _ratio(total, budget_invest)
        if diff > 0.05:
            out.append(_item('inv_mismatch', '投资勾稽', '申报投资与概算金额不一致', 'block',
                             f'申报总投资 {total:g} 万元，概算识别为 {budget_invest:g} 万元，'
                             f'相差 {diff:.1%}。',
                             '统一口径后重新提交；若含分期建设，请在投资章写明本期与总投资的关系。'))
        else:
            out.append(_item('inv_match', '投资勾稽', '申报投资与概算一致', 'pass',
                             f'申报 {total:g} 万元，概算 {budget_invest:g} 万元，'
                             f'相差 {diff:.1%}，在 5% 容差内。'))
    elif total and not budget_invest:
        out.append(_item('inv_unknown', '投资勾稽', '概算金额未能识别', 'warn',
                         '概算材料中没有识别到「总投资 X 万元」的表述，无法与申报投资核对。',
                         '在概算封面或汇总表写明总投资金额；也可手工在项目信息中核对。'))

    # 概算表内的「合计」行与申报投资比对
    if total and budgets:
        sums = []
        for b in budgets:
            for m in _TOTAL_PAT.findall(b.text or ''):
                try:
                    v = float(m.replace(',', ''))
                except ValueError:
                    continue
                if 10 <= v <= 1_000_000:
                    sums.append(v)
        if sums:
            top = max(sums)
            diff = _ratio(total, top)
            if diff > 0.1:
                out.append(_item('inv_sum', '投资勾稽', '概算合计行与申报投资差距较大', 'warn',
                                 f'概算表「合计」识别为 {top:g} 万元，'
                                 f'与申报 {total:g} 万元相差 {diff:.1%}。',
                                 '核对是否漏计工程建设其他费、预备费；也可能是识别到分项合计，请人工确认。'))
            else:
                out.append(_item('inv_sum', '投资勾稽', '概算合计与申报投资基本吻合', 'pass',
                                 f'概算合计 {top:g} 万元，申报 {total:g} 万元。'))

    if not out:
        out.append(_item('inv_none', '投资勾稽', '无可核对的投资数据', 'warn',
                         '申报投资或概算内容缺失，未能做金额勾稽。',
                         '补齐投资额与概算后重新预审。'))
    return out


# ------------------------------------------------------------------ 二级：AI
_SECTION_LIMIT = 6000
_MAX_CHUNKS = 5          # 最多审 5 章；按优先级挑
_MAX_PARTS = 4           # 单章超长时最多切 4 段（2.4 万字），控制调用次数但不再静默漏半章
_PRIORITY = ['necessity', 'demand', 'plan', 'invest', 'perf', 'tech', 'risk']

SYSTEM_PROMPT = """你是政务信息化项目的可研评审专家，为项目主管部门做技术审查。
你的任务是逐章找出材料中的实质性问题，不写评语、不写结论、不复述原文。

审查要点：
1. 必要性：现状问题是否有数据支撑，是否说明不建会怎样，是否存在"为建而建"。
2. 需求：需求是否来自实际业务量与用户群体，指标是否可量化，是否与现状能力对应。
3. 建设内容：功能清单是否与需求一一对应，是否有明显超出需求的功能；
   是否把已有系统的功能换名重报（注意"升级""改造""扩展"等表述）；
   是否与已知同类系统重复。
4. 技术方案：架构是否与本单位既有环境兼容，是否存在过度设计或明显缺项。
5. 投资：投资构成是否与建设内容匹配，是否有明显偏高或漏项（如只算软件不算集成、
   只算建设不算运维）。
6. 绩效与风险：绩效指标是否可考核，风险与应对是否具体。

只输出 JSON 数组，不要任何解释文字、不要代码围栏。每个元素四个字段：
{"level":"block","title":"问题一句话（不超过30字）",
 "quote":"材料原文片段（不超过80字，必须逐字摘自材料，找不到就留空字符串）",
 "issue":"问题说明：具体哪里不足，为什么是问题（60-150字）",
 "advice":"修改建议：让申报单位怎么改（30-100字）"}

level 只能取这三个英文词，不要写"高/中/低"、不要写中文：
block = 不补正无法评审（关键内容缺失、投资无依据、明显重复建设）
warn  = 影响评审质量需说明（论证薄弱、指标不可量化、内容与需求脱节）
info  = 建议优化（表述、结构、细节完善）

最多 6 条，按严重程度从高到低排列。没有实质问题就返回 []，不要为了凑数编问题。"""

# 模型偶尔无视「只能英文」的约束，用中文等级；这里兜住，别把它当默认 warn 混进去
_LEVEL_ALIAS = {
    'block': 'block', 'high': 'block', 'severe': 'block',
    '高': 'block', '严重': 'block', '必须': 'block', '高优先级': 'block',
    'warn': 'warn', 'medium': 'warn', 'middle': 'warn',
    '中': 'warn', '一般': 'warn', '需说明': 'warn', '中等': 'warn',
    'info': 'info', 'low': 'info', 'minor': 'info',
    '低': 'info', '建议': 'info', '提示': 'info',
}


def _norm_level(v):
    s = str(v or '').strip().lower()
    if s in _LEVEL_ALIAS:
        return _LEVEL_ALIAS[s]
    for k, val in _LEVEL_ALIAS.items():     # 形如「高（block）」之类
        if k in s:
            return val
    return 'warn'



def _drop_stale(project, level):
    """同一轮里重跑预审时，清掉上一份同级报告。

    预审是「当前材料」的体检结论，材料没变、轮次没变时重跑，结论只有一份有效；
    留着旧的会出现两份互相矛盾的报告，用户不知道该信哪份。跨轮的保留，那是历史。
    执行动作本身记在项目时间轴（ProjectEvent）里，不靠报告条数留痕。
    """
    PrecheckReport.objects.filter(project=project, round=project.round,
                                  level=level).delete()


def _ask_section(llm, messages, max_tokens):
    """要一段 JSON 结果。模型偶尔会写成散文或带一堆说明，这时追问一次。

    返回 (content, usage, tries)。截断的情况不重试 —— 那是额度问题，再问也一样，
    由调用方标成「未审成」让人补看。
    """
    content, usage, tries = '', {}, 0
    for i in range(2):
        tries = i + 1
        content, usage = llm.chat(messages, temperature=0.1, max_tokens=max_tokens)
        if _parse_findings(content)['ok'] or usage.get('finish_reason') == 'length':
            return content, usage, tries
        messages = messages + [
            {'role': 'assistant', 'content': (content or '（空）')[:300]},
            {'role': 'user',
             'content': '上面的输出不是可解析的 JSON 数组。请只输出 JSON 数组，'
                        '不要任何解释文字、不要代码围栏。'},
        ]
    return content, usage, tries


def run_ai_review(project, user):
    from apps.aicore import llm

    full_cfg = llm.load_config()
    cfg = full_cfg['llm']

    # 涉敏材料不出网：这个判断必须在任何正文取出、任何 HTTP 调用之前，
    # 否则「跳过」只是一句提示语，材料照样发给了外部模型
    if project.sensitive and (full_cfg.get('sensitive') or {}).get('skip_ai', True):
        raise RuntimeError(
            '本项目已标记为「涉敏材料」，按系统设置跳过 AI 深度审查，'
            '只跑规则预审，材料不外发。如确需送审，请管理员在「系统设置」里关闭该开关')

    if not cfg.get('enabled'):
        raise RuntimeError('AI 深度审查未启用。请管理员在「系统设置」里填写模型地址、'
                           '模型名称与密钥，勾选启用并测试通过后再使用')
    if not (cfg.get('base_url') and cfg.get('model')):
        raise RuntimeError('模型地址或模型名称未填写，请先在「系统设置」里配置')

    files, _text, sections = collect(project, corpus_only=True)
    # 通读用整章（chapters），不是细碎小节；老材料没存整章的退回小节
    chapters = []
    for f in files:
        chapters.extend(f.chapters or [])
    if not chapters:
        chapters = sections
    if not chapters:
        raise RuntimeError('材料未解析出章节，无法做逐章审查')

    # 每类最多审 2 章（审查项 #14）：原来一类只取第一章，「需求分析（二）（三）」
    # 里的实质问题永远看不到。章数仍受 _MAX_CHUNKS 总上限约束。
    picked = []
    for cat in _PRIORITY:
        take = 0
        for s in chapters:
            if s.get('category') == cat and len(s.get('text') or '') > 200:
                picked.append(s)
                take += 1
                if take >= 2:
                    break
    if not picked:
        picked = sorted(chapters, key=lambda s: -len(s.get('text') or ''))[:_MAX_CHUNKS]
    picked = picked[:_MAX_CHUNKS]

    # 真可研单章动辄上万字，只取前 6000 字等于后半章没看。
    # 超长的按段送审（每章最多两段），宁可多一次调用，也不漏内容。
    tasks = []
    for s in picked:
        raw_text = s.get('text') or ''
        parts = [raw_text[i:i + _SECTION_LIMIT]
                 for i in range(0, min(len(raw_text), _SECTION_LIMIT * _MAX_PARTS),
                                _SECTION_LIMIT)] or ['']
        for i, part in enumerate(parts):
            tasks.append((s, part, i + 1, len(parts)))

    findings, used, errors, blanks = [], [], [], []
    t0 = time.time()
    # 单章超过可审上限的部分不能静默丢弃（审查项 #14）：留一条未审记录，
    # 结论里写清楚「有 XX 字未送审」，不允许显示成干净通过。
    for s in picked:
        raw = s.get('text') or ''
        if len(raw) > _SECTION_LIMIT * _MAX_PARTS:
            skipped = len(raw) - _SECTION_LIMIT * _MAX_PARTS
            blanks.append(f'{s.get("title") or "未命名章节"}：超长未审 {skipped} 字')
    for s, body, part_no, part_total in tasks:
        title = s.get('title') or ''
        scope = (f'当前审查章节：{title}' if part_total == 1
                 else f'当前审查章节：{title}（本章共 {part_total} 段，这是第 {part_no} 段）')
        user_msg = (f'项目名称：{project.name}\n申报单位：{project.unit}\n'
                    f'总投资：{project.investment_wan or "未填"} 万元\n'
                    f'{scope}\n\n材料原文：\n{body}')
        label = title if part_total == 1 else f'{title}(第{part_no}段)'
        try:
            content, usage, tries = _ask_section(
                llm,
                [{'role': 'system', 'content': SYSTEM_PROMPT},
                 {'role': 'user', 'content': user_msg}],
                # 推理型模型先花 token 思考（实测单章吃掉 800-2000），
                # 额度给少了 content 会是空的；这里兜一个下限，别被设置页写小了
                max(int(cfg.get('max_tokens') or 8000), 6000),
            )
        except llm.LLMError as exc:
            errors.append(f'{label}: {exc}')
            continue

        parsed = _parse_findings(content)
        entry = {'title': label, 'usage': usage, 'chars': len(body),
                 'ok': parsed['ok'], 'raw_chars': len(content or ''),
                 'finish': usage.get('finish_reason'), 'tries': tries}
        used.append(entry)

        if not parsed['ok']:
            # 空内容或非 JSON：绝不能当成「没发现问题」，否则等于给材料盖章通过
            if usage.get('finish_reason') == 'length':
                why = (f'输出被 max_tokens 截断（推理占用 '
                       f'{usage.get("reasoning_tokens") or "?"} token）')
            else:
                why = '返回内容不是可解析的 JSON'
            entry['why'] = why
            # 留一段原始返回，运维排查时能直接看到模型到底吐了什么
            entry['raw_head'] = (content or '')[:400]
            blanks.append(f'{label}: {why}')
            continue

        for f in parsed['items']:
            f['section'] = title
            f['category'] = s.get('category') or 'other'
            findings.append(f)

    ok_chunks = [u for u in used if u['ok']]
    if not ok_chunks:
        raise RuntimeError('模型没有返回可用的审查结果：' + '；'.join(blanks[:3] or errors[:3]))

    # 同一问题在相邻章节重复出现时只留级别最高的那条
    seen, deduped = {}, []
    for f in findings:
        k = _norm(f.get('title', ''))[:24]
        if k in seen:
            if _level_weight(f.get('level')) > _level_weight(seen[k]['level']):
                deduped[deduped.index(seen[k])] = f
                seen[k] = f
            continue
        seen[k] = f
        deduped.append(f)

    # 全局限流：一次甩二十几条没人看得完。但每章至少留一条 ——
    # 否则某一章审了却一条都不显示，用户会以为这章没问题。
    limit = 12
    dropped = 0
    if len(deduped) > limit:
        keep = {id(f) for f in deduped if f['level'] == 'block'}
        by_section = {}
        for f in deduped:
            by_section.setdefault(f.get('section') or '', []).append(f)
        for items in by_section.values():
            if any(id(i) in keep for i in items):
                continue
            top = max(items, key=lambda x: _level_weight(x['level']))
            keep.add(id(top))
        for f in deduped:
            if len(keep) >= limit:
                break
            if f['level'] != 'block':
                keep.add(id(f))
        dropped = len(deduped) - len(keep)
        deduped = [f for f in deduped if id(f) in keep]

    counts = {k: sum(1 for f in deduped if f.get('level') == k)
              for k in ('block', 'warn', 'info')}
    counts['total'] = len(deduped)
    counts['unread'] = len(blanks)
    verdict = (PrecheckReport.Verdict.BLOCK if counts['block']
               else PrecheckReport.Verdict.ATTENTION if counts['warn']
               else PrecheckReport.Verdict.PASS)
    # 有章节没审成时不可能是「通过」：没看过的内容不能算没问题
    if blanks and verdict == PrecheckReport.Verdict.PASS:
        verdict = PrecheckReport.Verdict.ATTENTION

    tokens = sum(int((u['usage'] or {}).get('total_tokens') or 0) for u in used)
    note = (f'审查 {len(ok_chunks)}/{len(used)} 章：'
            + '、'.join(u['title'] or '' for u in ok_chunks)
            + f'；耗时 {int(time.time() - t0)} 秒，消耗 {tokens} token')
    if blanks:
        note += '；以下章节未取得有效结果，需人工补看：' + '；'.join(blanks[:3])
    if dropped:
        note += f'；另有 {dropped} 条较轻问题未展开（避免一次列太多）'
    if errors:
        note += '；调用失败：' + '；'.join(errors[:2])

    _drop_stale(project, PrecheckReport.Level.AI)
    report = PrecheckReport.objects.create(
        project=project, round=project.round, level=PrecheckReport.Level.AI,
        verdict=verdict, counts=counts, findings=deduped, note=note,
        llm_info={'model': cfg.get('model'), 'base_url': cfg.get('base_url'),
                  'chunks': used},
        created_by=user,
    )
    return report


def _level_weight(level):
    return {'block': 3, 'warn': 2, 'info': 1}.get(level, 0)


_OBJ_PAT = re.compile(r'\{[^{}]*\}')


def _parse_findings(content):
    """模型返回的 JSON 经常带围栏、前后废话，或者被 max_tokens 截断。

    返回 {'ok': bool, 'items': [...]}：
    ok=False 表示没拿到可用结果（空内容、非 JSON、截断到一条完整的都没有），
    这时调用方必须当成「这章没审成」，绝不能当成「这章没问题」。
    截断时若前面几条完整，逐个抢救出来，至少不白花钱。
    """
    text = (content or '').strip()
    if not text:
        return {'ok': False, 'items': []}

    raw = text
    if '```' in raw:
        raw = re.sub(r'^.*?```(?:json)?', '', raw, flags=re.S)
        raw = raw.split('```')[0]
    start, end = raw.find('['), raw.rfind(']')
    if start >= 0 and end > start:
        raw = raw[start:end + 1]

    data = None
    try:
        # strict=False：模型常在字符串里塞裸换行，标准 JSON 会判非法，这里放过
        data = json.loads(raw, strict=False)
    except Exception:
        data = None

    if isinstance(data, dict):
        data = data.get('findings') or data.get('items') or []

    rows = data if isinstance(data, list) else None
    if rows is None:
        # 整体解析失败：逐个抠对象，能捞几条算几条（截断抢救）
        rows = []
        for chunk in _OBJ_PAT.findall(text):
            try:
                obj = json.loads(chunk, strict=False)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get('title'):
                rows.append(obj)

    out = []
    for d in rows:
        if not isinstance(d, dict):
            continue
        title = str(d.get('title') or '').strip()[:60]
        if not title:
            continue
        out.append({
            'level': _norm_level(d.get('level')),
            'title': title,
            'quote': str(d.get('quote') or '').strip()[:200],
            'issue': str(d.get('issue') or '').strip()[:600],
            'advice': str(d.get('advice') or '').strip()[:400],
        })

    if not out and not (isinstance(data, list) and data == []):
        # 既没捞出条目，也不是明确的空数组 → 视为没审成
        return {'ok': False, 'items': []}
    return {'ok': True, 'items': out[:6]}
