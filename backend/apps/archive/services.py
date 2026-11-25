"""档案导入与解析的编排逻辑。视图只负责收参数，流程都在这里。"""
import hashlib
import logging
import time
from pathlib import Path

from django.db import transaction

from . import extract, functions, similarity, textparse
from .models import ArchiveFile, ArchiveProject

log = logging.getLogger(__name__)

MAX_BYTES = 200 * 1024 * 1024  # 单份材料 200MB 上限，超过的 PDF 基本是扫描件
MAX_CHARS = 800_000           # 存库正文上限，再长的截断（比对用不到那么多）


def sha256_of(django_file) -> str:
    h = hashlib.sha256()
    for chunk in django_file.chunks():
        h.update(chunk)
    django_file.seek(0)
    return h.hexdigest()


def guess_doc_type(filename: str) -> str:
    n = filename.lower()
    if '建议书' in n:
        return ArchiveFile.DocType.PROPOSAL
    if '概算' in n or '估算' in n or '预算' in n:
        return ArchiveFile.DocType.BUDGET
    if '批复' in n or '备案' in n:
        return ArchiveFile.DocType.APPROVAL
    if '可研' in n or '可行性' in n:
        return ArchiveFile.DocType.FEASIBILITY
    return ArchiveFile.DocType.OTHER


def parse_into_record(rec: ArchiveFile, year_hint=None) -> ArchiveFile:
    """解析已落盘的文件，把正文、章节、词频指纹、元数据写回记录。"""
    t0 = time.time()
    rec.status = ArchiveFile.Status.PARSING
    rec.save(update_fields=['status'])
    try:
        path = Path(rec.file.path)
        if not path.exists():
            raise FileNotFoundError('原件不存在，请重新上传')

        raw_text, pages = textparse.parse_file(path)
        text = textparse.clean_text(raw_text)
        if len(text) < 200:
            raise ValueError(
                f'只解析出 {len(text)} 个字符。若是扫描件（没有文字层），'
                '需要先做 OCR 再上传；若原件本身很短，请确认是否传错文件'
            )

        sections = textparse.split_sections(text)
        for s in sections:
            s['chars'] = len(s['text'])

        meta = extract.extract(text, rec.filename, year_hint)

        rec.text = text[:MAX_CHARS]
        rec.sections = sections
        rec.grams = similarity.build_grams(text)
        rec.functions = functions.extract_functions(sections)
        rec.text_hash = similarity.text_fingerprint(text)
        rec.page_count = pages or 0
        rec.char_count = len(text)
        rec.status = ArchiveFile.Status.READY
        rec.error = ''
        rec.save(update_fields=['text', 'sections', 'grams', 'functions', 'text_hash',
                                'page_count', 'char_count', 'status', 'error'])
        log.info('解析完成 %s：%d 字 / %d 章 / %d 项功能 / %.1fs',
                 rec.filename, rec.char_count, len(sections),
                 len(rec.functions or []), time.time() - t0)
        return rec, meta
    except Exception as exc:  # 解析失败不影响其他文件
        rec.status = ArchiveFile.Status.FAILED
        rec.error = str(exc)[:500]
        rec.save(update_fields=['status', 'error'])
        log.warning('解析失败 %s：%s', rec.filename, exc)
        return rec, None


def resolve_project(meta, user, project=None, doc_type=None) -> ArchiveProject:
    """按项目名称挂靠：同名的并到一处，不同名的新建。"""
    if project is not None:
        return project
    name = (meta or {}).get('name') or '未命名材料'
    # 原来只按 name 匹配：两个单位都叫"智慧城市建设项目"时材料混进同一条
    # 档案，判重与统计口径全乱。按 (name, unit) 联合定位，单位为空时用空串，
    # 让"同单位同名"仍合并、"跨单位同名"分开。
    unit = (meta or {}).get('unit') or ''
    obj = ArchiveProject.objects.filter(name=name, unit=unit).first()
    if obj is None:
        obj = ArchiveProject.objects.create(
            name=name,
            unit=(meta or {}).get('unit') or '',
            year=(meta or {}).get('year'),
            year_source=(meta or {}).get('year_source') or '',
            investment=(meta or {}).get('investment'),
            created_by=user,
        )
    else:
        changed = []
        for f in ('unit', 'year', 'investment'):
            if not getattr(obj, f) and (meta or {}).get(f):
                setattr(obj, f, meta[f])
                changed.append(f)
        # 只有「年度是新补上的」才记来源；已经有人填过的年度不动，
        # 否则重新导入一份材料就把人工核准的年度冲掉了
        if 'year' in changed:
            obj.year_source = (meta or {}).get('year_source') or ''
            changed.append('year_source')
        if changed:
            obj.save(update_fields=changed + ['updated'])
    return obj


def ingest_upload(uploaded, user, project=None, doc_type=None, year=None):
    """处理一个上传文件。返回 dict：{ok, skipped, file, meta, message}"""
    filename = uploaded.name or 'unnamed'
    ext = Path(filename).suffix.lower()
    if ext in getattr(textparse, 'SHEET_EXT', set()):
        # 档案收录的是可研、批复这类有正文的材料；预算表在项目里留底即可
        return {'ok': False, 'skipped': False, 'filename': filename,
                'message': '历史档案只收可研、批复这类正文材料，Excel 表格请在项目'
                           '「申报材料」里上传'}
    if ext in textparse.LEGACY:
        return {'ok': False, 'skipped': False, 'filename': filename,
                'message': f'暂不支持 {ext} 格式，请在 Word 里另存为 .docx'}
    if ext not in textparse.SUPPORTED:
        return {'ok': False, 'skipped': False, 'filename': filename,
                'message': f'不支持的文件类型 {ext}，支持 PDF / DOCX / TXT'}
    if uploaded.size > MAX_BYTES:
        return {'ok': False, 'skipped': False, 'filename': filename,
                'message': f'文件 {uploaded.size / 1048576:.0f}MB 超过 200MB 上限'}

    digest = sha256_of(uploaded)
    dup = ArchiveFile.objects.filter(sha256=digest, status=ArchiveFile.Status.READY).first()
    if dup:
        return {'ok': True, 'skipped': True, 'id': dup.id, 'filename': filename,
                'project_id': dup.project_id, 'meta': None,
                'message': f'内容与已有材料《{dup.filename}》完全相同，已跳过'}

    rec = ArchiveFile(
        project=project,
        filename=filename,
        size=uploaded.size,
        sha256=digest,
        doc_type=doc_type or guess_doc_type(filename),
        uploaded_by=user,
        status=ArchiveFile.Status.PENDING,
    )
    # 先落盘（project 暂可为空，解析后确定归属再补）
    rec.file.save(filename, uploaded, save=False)
    placeholder = None
    if rec.project_id is None:
        # project 是必填外键，先建一个占位项目，解析后按名称合并
        placeholder = ArchiveProject.objects.create(name='（待归入）', created_by=user)
        rec.project = placeholder
    rec.save()

    rec, meta = parse_into_record(rec, year_hint=year)
    if meta is None:
        error = rec.error or '解析失败'
        # 失败不留半截：删记录（信号顺带删原件），占位项目空了也一并删掉。
        # 原来这里直接 return，失败材料和「（待归入）」在档案列表里越积越多
        rec.delete()
        if placeholder is not None and not placeholder.files.exists():
            placeholder.delete()
        return {'ok': False, 'skipped': False, 'id': None, 'filename': filename,
                'message': error}

    target = resolve_project(meta, user, project=project, doc_type=rec.doc_type)
    if rec.project_id != target.id:
        placeholder = rec.project
        rec.project = target
        rec.save(update_fields=['project'])
        if placeholder.name == '（待归入）' and not placeholder.files.exists():
            placeholder.delete()

    return {'ok': True, 'skipped': False, 'id': rec.id, 'filename': filename,
            'project_id': target.id, 'meta': meta,
            'chars': rec.char_count, 'sections': len(rec.sections),
            'message': f'解析完成，{rec.char_count} 字，{len(rec.sections)} 章'}


# ---------------------------------------------------------------- 比对

def _as_year(value):
    """年度只收整数。前端传 'abc' 时不能让 int() 在比对深处抛成 500。"""
    if value in (None, ''):
        return None
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2200 else None


def compare_sections_of(sections):
    """取出参与重复建设比对的章节。

    只认「建设内容与方案」「需求分析」两章。政务可研的其余章节模板化程度极高，
    两份八竿子打不着的材料在「必要性」「安全保障」「测评方案」上都能到八九成，
    把这些算进去，分数就从「像不像」退化成「是不是同一套模板」。
    """
    return [s for s in textparse.retag(sections)
            if s.get('category') in textparse.COMPARE_CATEGORIES]


def compare_text_of(sections):
    """参与比对的正文。识别不到建设内容章时返回空串，由调用方决定是否退回全文。"""
    return '\n'.join(s.get('text') or '' for s in compare_sections_of(sections))


def build_query(payload):
    """把比对输入规整成统一结构：文本、名称、单位、年度、投资、功能项。"""
    text = payload.get('text') or ''
    sections = textparse.split_sections(text)
    for s in sections:
        s['chars'] = len(s['text'])
    scope = compare_text_of(sections)
    # 没有建设内容章（材料没分节，或章节标题不规范）时退回全文：
    # 退回是「降级」，不是「等价」，前端要据此提示用户肉眼复核
    q_grams = similarity.build_grams(scope) or similarity.build_grams(text)
    meta = extract.extract(text, payload.get('filename') or payload.get('name') or '')
    q = {
        'name': payload.get('name') or meta['name'] or '待比对材料',
        'unit': payload.get('unit') or meta['unit'],
        'year': _as_year(payload.get('year')) or meta['year'],
        'investment': payload.get('investment') or meta['investment'],
        'text': text,
        'sections': sections,
        'grams': q_grams,
        'functions': functions.extract_functions(textparse.retag(sections)),
        'scope': {
            'fallback': not scope.strip(),
            'chars': len(scope),
            'chapters': [s.get('title') for s in compare_sections_of(sections)][:12],
        },
    }
    q['is_upgrade'] = functions.upgrade_hint(text, sections)['is_upgrade']
    return q


def run_compare(q, scope_project_ids=None, top_n=8, snippet_top=5,
                include_snippets=True, exclude_file_ids=None, max_docs=500):
    """把待比对材料与历史档案逐份比，按项目汇总取最像的一份。"""
    t0 = time.time()
    qs = ArchiveFile.objects.filter(status=ArchiveFile.Status.READY)
    if scope_project_ids:
        qs = qs.filter(project_id__in=scope_project_ids)
    if exclude_file_ids:
        qs = qs.exclude(id__in=exclude_file_ids)
    docs = list(qs.select_related('project')[:max_docs])

    q_paras = q_sets = None
    if include_snippets:
        q_paras = similarity.split_paragraphs(
            q['sections'], categories=textparse.COMPARE_CATEGORIES)
        q_sets = [similarity.gram_set(similarity.build_grams(p['text'],
                                                             top=400, keep_filler=True))
                  for p in q_paras]

    by_project = {}
    for doc in docs:
        # 两侧必须同一口径：只比建设内容章。拿整篇词频去比「只取建设内容」的
        # 词频，分母不一致，算出来的相似度没有意义
        d_scope = compare_text_of(doc.sections or [])
        d_grams = similarity.build_grams(d_scope) or doc.grams
        if not d_grams:
            continue
        d = {
            'name': doc.project.name,
            'unit': doc.project.unit,
            'year': doc.project.year,
            'investment': doc.project.investment,
            'grams': d_grams,
            'sections': doc.sections or [],
            'functions': doc.functions or [],
            'text': doc.text or '',
            'is_upgrade': False,
        }
        res = similarity.compare_one(q, d)
        if not res:
            continue
        res['project_id'] = doc.project_id
        res['project_name'] = doc.project.name
        res['unit'] = doc.project.unit
        res['year'] = doc.project.year
        res['investment'] = float(doc.project.investment) if doc.project.investment else None
        res['status'] = doc.project.status
        res['file_id'] = doc.id
        res['filename'] = doc.filename
        res['doc_type'] = doc.get_doc_type_display()

        best = by_project.get(doc.project_id)
        if best is None or res['scores']['relevance'] > best['scores']['relevance']:
            by_project[doc.project_id] = res

    # 排序按疑点级别优先，其次相关度 —— 换名改造的相关度不一定最高，但疑点最实
    ranking = sorted(by_project.values(),
                     key=lambda r: (-r['screen']['weight'], -r['scores']['relevance']))[:top_n]

    # 只给最像的几份补片段，后面那些分数低的补了也没人看，白花时间
    if include_snippets:
        for item in ranking[:3]:
            doc = next((d for d in docs if d.id == item['file_id']), None)
            if not doc:
                item['snippets'] = []
                continue
            d_paras = similarity.split_paragraphs(
                doc.sections or [], categories=textparse.COMPARE_CATEGORIES)
            d_sets = [similarity.gram_set(similarity.build_grams(p['text'],
                                                                 top=400, keep_filler=True))
                      for p in d_paras]
            item['snippets'] = similarity.find_snippets(q_paras, q_sets, d_paras, d_sets,
                                                        top=snippet_top)
        for item in ranking[3:]:
            item['snippets'] = []

    top = ranking[0] if ranking else None
    if top:
        headline = {
            'level': top['screen']['level'],
            'level_label': top['screen']['level_label'],
            'label': top['screen']['summary'],
            'attention': top['finding_counts']['attention'],
            'verify': top['finding_counts']['verify'],
            'info': top['finding_counts']['info'],
            'project': top['project_name'],
            'note': '只比对建设内容（含需求分析）章。必要性、政策依据、安全保障、'
                    '测评方案等通用章节模板化程度高，不参与比对。系统只做疑点筛查，'
                    '是否构成重复建设由人工核定，核定结果记录在案。',
        }
    else:
        headline = {
            'level': 'none', 'level_label': '无可比对材料', 'label': '没有可比对的历史材料',
            'attention': 0, 'verify': 0, 'info': 0, 'project': '',
            'note': '档案库为空，或没有解析成功的材料。比对无法进行，不代表不存在重复。',
        }
    # 全库疑点合计：不只报最像的那一个，避免专家只看第一条
    total_findings = sum(r['finding_counts']['attention'] + r['finding_counts']['verify']
                         for r in ranking)

    return {
        'query': {
            'name': q['name'], 'unit': q['unit'], 'year': q['year'],
            'investment': q['investment'],
            'chars': len(q['text']), 'sections': len(q['sections']),
            'functions': len([f for f in q['functions'] if not f['generic']]),
            'is_upgrade': q.get('is_upgrade', False),
            'hash': similarity.text_fingerprint(q['text']),
            'scope': q.get('scope') or {},
        },
        'headline': headline,
        'summary': {
            'compared': len(by_project),
            'elapsed_ms': int((time.time() - t0) * 1000),
            'scanned_files': len(docs),
            'findings': total_findings,
        },
        'ranking': ranking,
    }
