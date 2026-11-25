"""申报材料入库与解析。

与历史档案共用一套解析算法，但落库到 ProjectFile —— 两边的生命周期不同：
档案只管「过去批过什么」，申报材料要跟着项目走完流程、被预审和评审反复读，
并且删除项目时要一起清掉。

解析在这里同步完成：可研一般几十页，两三秒就能出结果，用户传完就能看到
「解析完成，X 字 / Y 章」，比异步任务 + 轮询少一层状态，也没必要。
"""
import hashlib
import logging
from pathlib import Path

from apps.archive import extract as archive_extract
from apps.archive import functions as archive_functions
from apps.archive import similarity as archive_similarity
from apps.archive import textparse

from .models import Project, ProjectFile

log = logging.getLogger(__name__)

MAX_BYTES = 200 * 1024 * 1024
MAX_CHARS = 800_000
ALLOWED_DOC_TYPES = {c[0] for c in ProjectFile.DocType.choices}


class UploadError(Exception):
    pass


def _sha256(uploaded):
    h = hashlib.sha256()
    for chunk in uploaded.chunks():
        h.update(chunk)
    uploaded.seek(0)
    return h.hexdigest()


def guess_doc_type(filename):
    n = (filename or '').lower()
    if '建议书' in n:
        return ProjectFile.DocType.PROPOSAL
    if '概算' in n or '估算' in n or '预算' in n:
        return ProjectFile.DocType.BUDGET
    if '批复' in n or '备案' in n or '依据' in n:
        return ProjectFile.DocType.APPROVAL
    if '可研' in n or '可行性' in n or '方案' in n:
        return ProjectFile.DocType.FEASIBILITY
    return ProjectFile.DocType.OTHER


def ingest(project, uploaded, user, doc_type=''):
    """上传并解析一份材料。返回 ProjectFile；失败抛 UploadError。"""
    filename = uploaded.name or 'unnamed'
    ext = Path(filename).suffix.lower()
    if ext in getattr(textparse, 'SHEET_LEGACY', set()):
        raise UploadError(f'暂不支持 {ext} 旧版表格，请用 Excel 另存为 .xlsx 后重传')
    if ext in textparse.LEGACY:
        raise UploadError(f'暂不支持 {ext} 格式，请在 Word 里另存为 .docx 后重传')
    if ext not in textparse.SUPPORTED:
        raise UploadError('不支持的文件类型 {}，支持 PDF / DOCX / TXT / XLSX'.format(ext))
    if uploaded.size > MAX_BYTES:
        raise UploadError(f'文件 {uploaded.size / 1048576:.0f}MB 超过 200MB 上限')

    if doc_type not in ALLOWED_DOC_TYPES:
        doc_type = guess_doc_type(filename)

    digest = _sha256(uploaded)
    if project.files.filter(sha256=digest).exists():
        raise UploadError(f'《{filename}》已在本项目中上传过，不要重复上传')

    # 同类型材料的版本：补正后再传一份可研，不是「多了一个文件」，
    # 而是「上一版被这一版取代」。旧版保留，只是标成非当前
    same_kind = project.files.filter(doc_type=doc_type)
    version = (same_kind.order_by('-version').values_list('version', flat=True).first()
               or 0) + 1
    same_kind.update(is_current=False)

    rec = ProjectFile(project=project, filename=filename, size=uploaded.size,
                      sha256=digest, doc_type=doc_type, uploaded_by=user,
                      round=project.round, stage=project.stage, version=version,
                      is_current=True, status=ProjectFile.Status.PARSING)
    rec.file.save(filename, uploaded, save=False)
    rec.save()

    # 表格（Excel）不是可研正文：不切章节、不进查重与功能抽取，只存文字供预览检索。
    # 合格门槛也不能按 200 字卡 —— 一张预算表往往几十字加一堆数字，按正文口径永远是失败
    is_sheet = ext in getattr(textparse, 'SHEET_EXT', set())
    # 第三方《费用评估报告》同理：它是审批依据，不是申报文本。不切章节、不进查重池
    # （报告与可研本来就会大段重合，进池只会造出一堆假重复），也不回填项目字段。
    # 门槛放到 0 字：财评机构交来的多是签字盖章的扫描件，没有文字层很常见，
    # 卡 200 字会让「材料已经收到」显示成「解析失败」，反过来误导审核岗
    is_report = doc_type == ProjectFile.DocType.COST_REPORT
    try:
        path = Path(rec.file.path)
        raw, pages = textparse.parse_file(path)
        text = textparse.clean_text(raw)
        floor = 0 if is_report else (1 if is_sheet else 200)
        if len(text) < floor:
            raise ValueError(
                '这张表里没有读到文字，请确认单元格不是图片或截图'
                if is_sheet else
                f'只解析出 {len(text)} 个字符，可能是扫描件（没有文字层）。'
                '请用带文字层的 PDF，或先做 OCR 再上传')
        if is_sheet or is_report:
            rec.text = text[:MAX_CHARS]
            rec.sections = []
            rec.chapters = []
            rec.grams = {}
            rec.functions = []
            rec.page_count = pages or 0
            rec.char_count = len(text)
            rec.status = ProjectFile.Status.READY
            rec.error = ''
            rec.save(update_fields=['text', 'sections', 'chapters', 'grams', 'functions',
                                    'page_count', 'char_count', 'status', 'error'])
            rec.filled_fields = []      # 预算表与评估报告都不回填项目字段
            return rec
        sections = textparse.split_sections(text)
        for s in sections:
            s['chars'] = len(s['text'])
        chapters = textparse.split_chapters(text)

        rec.text = text[:MAX_CHARS]
        rec.sections = sections
        rec.chapters = chapters
        rec.grams = archive_similarity.build_grams(text)
        rec.functions = archive_functions.extract_functions(sections)
        rec.page_count = pages or 0
        rec.char_count = len(text)
        rec.status = ProjectFile.Status.READY
        rec.error = ''
        rec.save(update_fields=['text', 'sections', 'chapters', 'grams', 'functions',
                                'page_count', 'char_count', 'status', 'error'])
    except Exception as exc:
        rec.status = ProjectFile.Status.FAILED
        rec.error = str(exc)[:500]
        # 上传前已把旧版置为非当前；这一份没解析成功，不能让它顶掉旧版
        rec.is_current = False
        rec.save(update_fields=['status', 'error', 'is_current'])
        prev = (project.files.filter(doc_type=doc_type)
                .exclude(pk=rec.pk).order_by('-version').first())
        if prev:
            prev.is_current = True
            prev.save(update_fields=['is_current'])
        log.warning('申报材料解析失败 %s：%s', filename, exc)
        return rec

    # 挂成内存属性而不落库：调用方要拿它告诉用户「哪几项已从材料里填上」，
    # 但这个信息只在本次请求内有意义，存库反而多一列要维护
    rec.filled_fields = _backfill_project(project, text, filename, sections)
    return rec


# 回填字段与中文名，前端要拿这个提示「这几项已从材料里填上」
BACKFILL_FIELDS = {
    'name': '项目名称', 'unit': '申报单位', 'investment': '总投资',
    'year': '申报年度', 'duration_months': '建设周期', 'summary': '建设内容',
    'necessity': '建设必要性', 'basis': '政策依据', 'category': '业务领域',
}


def _backfill_project(project, text, filename, sections=None):
    """材料里能认出来的元数据，回填到项目上空着的字段。

    只填空，不改已有值 —— 申报人自己填的名称和金额是他确认过的，
    机器识别错了改掉比空着更糟。返回被填上的字段名列表。
    """
    from . import prefill       # 延迟导入：prefill 反过来会用到本模块的常量

    if sections is None:
        sections = textparse.split_sections(text)
    try:
        got, _src = prefill.rule_extract(text, sections, filename)
    except Exception as exc:
        log.info('回填抽取失败 %s：%s', filename, exc)
        return []

    filled = []
    # 名称：太短的占位名（如「新建项目」）允许被材料里的正式名替换
    if len((project.name or '').strip()) < 4 and got.get('name'):
        project.name = got['name'][:200]
        filled.append('name')
    # 单位必须先过名录：存进去一个对不上的写法，提交时还会被拦
    if not (project.unit or '').strip() and got.get('unit'):
        from apps.core import org_units

        std, ok = org_units.normalize(got['unit'])
        if ok:
            project.unit = std[:120]
            filled.append('unit')
    if not project.investment and got.get('investment'):
        project.investment = got['investment']
        filled.append('investment')
    if not project.year and got.get('year'):
        project.year = got['year']
        filled.append('year')
    if not project.duration_months and got.get('duration_months'):
        project.duration_months = got['duration_months']
        filled.append('duration_months')
    if len((project.summary or '').strip()) < 30 and len(got.get('summary') or '') >= 30:
        project.summary = got['summary']
        filled.append('summary')
    if not (project.necessity or '').strip() and got.get('necessity'):
        project.necessity = got['necessity']
        filled.append('necessity')
    if not (project.basis or '').strip() and got.get('basis'):
        project.basis = got['basis']
        filled.append('basis')
    if not (project.category or '').strip() and got.get('category'):
        project.category = got['category']
        filled.append('category')
    if filled:
        project.save(update_fields=filled + ['updated'])
    return filled


def move_file(rec, doc_type):
    if doc_type not in ALLOWED_DOC_TYPES:
        raise UploadError('材料类型不正确')
    rec.doc_type = doc_type
    rec.save(update_fields=['doc_type'])
    return rec
