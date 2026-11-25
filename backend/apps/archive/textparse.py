"""材料解析与章节切分。

三种格式，三条路径：
  PDF   pdfplumber 取文字层，逐页清理页眉页脚（政务文档几乎每页都有）
  DOCX  python-docx 按 body 顺序遍历段落与表格 —— 表格必须取，
        因为投资概算、功能清单都在表里，只取正文段落等于漏掉最关键的数据
  TXT   直接读

解析后统一产出：正文 + 章节列表。章节切分是为了后面按「建设内容」「投资估算」
这类同类章节做对齐比对 —— 整篇粗暴比一次，会把模板话术的相似误判成内容重复。
"""
import re
from pathlib import Path

# ---------------------------------------------------------------- 章节归类

# 标准章节类别。政务可研的结构是高度模板化的，归类后才谈得上对齐比较。
CATEGORIES = {
    'necessity': '建设必要性',
    'demand': '需求分析',
    'plan': '建设内容与方案',
    'tech': '技术路线',
    'security': '安全保障',
    'data': '数据与共享',
    'invest': '投资估算',
    'schedule': '实施计划',
    'ops': '运维保障',
    'perf': '绩效目标',
    'risk': '风险分析',
    'other': '其他',
}

# 重复建设只比这两类章 —— 「要建什么」。
# 必要性、政策依据、安全保障、实施计划、测评方案这些章节在政务可研里
# 几乎逐字相同，比出来人人都是高分，反而把真正的重复淹掉。
COMPARE_CATEGORIES = ('plan', 'demand')

# 关键词 → 类别。匹配时按关键词长度倒序，避免「建设必要性」被「建设」抢走。
_CATEGORY_WORDS = [
    ('项目背景', 'necessity'), ('建设背景', 'necessity'), ('必要性', 'necessity'),
    ('建设意义', 'necessity'), ('项目概述', 'necessity'), ('总论', 'necessity'),
    ('概述', 'necessity'), ('建设依据', 'necessity'),
    ('现状', 'necessity'), ('存在问题', 'necessity'),
    ('需求分析', 'demand'), ('业务需求', 'demand'), ('功能需求', 'demand'),
    ('用户需求', 'demand'), ('需求', 'demand'),
    ('建设内容', 'plan'), ('建设方案', 'plan'), ('技术方案', 'plan'),
    ('总体设计', 'plan'), ('系统设计', 'plan'), ('功能设计', 'plan'),
    ('建设目标', 'plan'), ('主要任务', 'plan'), ('架构', 'plan'), ('方案', 'plan'),
    ('技术路线', 'tech'), ('技术选型', 'tech'), ('关键技术', 'tech'), ('技术', 'tech'),
    ('安全保障', 'security'), ('安全设计', 'security'), ('网络安全', 'security'),
    ('等保', 'security'), ('密码', 'security'), ('安全', 'security'),
    ('数据共享', 'data'), ('共享', 'data'), ('数据资源', 'data'),
    ('数据目录', 'data'), ('标准规范', 'data'), ('数据', 'data'),
    ('投资估算', 'invest'), ('投资概算', 'invest'), ('概算', 'invest'),
    ('估算', 'invest'), ('资金', 'invest'), ('经费', 'invest'), ('预算', 'invest'),
    ('实施计划', 'schedule'), ('实施进度', 'schedule'), ('进度', 'schedule'),
    ('工期', 'schedule'), ('建设周期', 'schedule'), ('计划', 'schedule'),
    ('运维', 'ops'), ('运行维护', 'ops'), ('售后服务', 'ops'), ('保障措施', 'ops'),
    ('组织保障', 'ops'),
    ('绩效', 'perf'), ('效益', 'perf'), ('考核', 'perf'),
    ('风险', 'risk'),
]
_CATEGORY_WORDS.sort(key=lambda kv: -len(kv[0]))

# 通用章节：几乎每份可研都要写、写的又都差不多，查重时一律排除。
#
# 只靠 CATEGORIES 归类不够：「（二）测评方案」「（1）网络架构」都带「方案」
# 「架构」，会被归成「建设内容与方案」混进比对范围；「网络安全需求分析」
# 带「需求」，会被归成需求分析。这些章节在项目之间本来就应该长得一样，
# 算出来的相似度越高，越说明不了问题，只是把真正的重复盖住。
# 所以先按这里拦截，命中就归到对应类别，永远进不了 COMPARE_CATEGORIES。
_GENERIC_WORDS = [
    ('测评', 'other'), ('检测', 'other'), ('验收方案', 'other'),
    ('等保', 'security'), ('等级保护', 'security'), ('密码应用', 'security'),
    ('密码', 'security'), ('网络安全', 'security'), ('安全保障', 'security'),
    ('安全设计', 'security'), ('安全', 'security'),
    ('网络架构', 'tech'), ('技术架构', 'tech'), ('部署架构', 'tech'),
    ('运维', 'ops'), ('运行维护', 'ops'), ('售后服务', 'ops'),
    ('培训', 'ops'), ('监理', 'ops'), ('组织保障', 'ops'), ('保障措施', 'ops'),
    ('实施计划', 'schedule'), ('实施进度', 'schedule'), ('进度安排', 'schedule'),
    ('投资估算', 'invest'), ('投资概算', 'invest'), ('资金筹措', 'invest'),
    ('绩效', 'perf'), ('效益分析', 'perf'),
    ('风险', 'risk'),
]
_GENERIC_WORDS.sort(key=lambda kv: -len(kv[0]))

_CHAPTER_RE = re.compile(r'^第[一二三四五六七八九十百]+[章篇部分]')
_CN_NUM_RE = re.compile(r'^[一二三四五六七八九十]+[、．.]')
_PAREN_CN_RE = re.compile(r'^[（(][一二三四五六七八九十]+[）)]')
_DOTTED_RE = re.compile(r'^\d+(\.\d+){1,2}')

_PAGE_NO_RE = re.compile(r'^[-—\s]*第?\s*\d+\s*页?[-—\s]*$|^\s*\d{1,3}\s*$')

# 很多可研的章节标题不带编号，就写「投资估算与资金筹措」。
# 只靠长度判断会把正文短句也切出来，所以要求同时命中章节关键词，
# 且不含句读标点 —— 正文句子几乎不可能同时满足这两条。
_HEADING_WORDS = (
    '项目概述', '总论', '概述', '建设背景', '项目背景', '建设依据',
    '必要性', '建设意义', '现状', '存在问题',
    '需求分析', '业务需求', '功能需求', '需求',
    '建设内容', '建设方案', '技术方案', '总体设计', '系统设计', '功能设计',
    '建设目标', '主要任务', '架构',
    '技术路线', '技术选型', '关键技术',
    '安全保障', '安全设计', '网络安全', '等保',
    '数据共享', '共享交换', '数据资源', '数据目录', '标准规范',
    '投资估算', '投资概算', '概算', '估算', '资金筹措', '资金来源',
    '实施计划', '实施进度', '进度安排', '建设周期',
    '运维', '运行维护', '售后服务', '保障措施', '组织保障',
    '绩效', '效益分析', '考核指标',
    '风险',
)
_HEADING_BAD = re.compile(r'[，。；：、,;:！？|]')



def classify(title: str) -> str:
    """把一个标题归到标准章节类别。

    先拦通用章节再走关键词表：顺序反了，「测评方案」会被「方案」抢去当建设内容。
    """
    t = title or ''
    for word, cat in _GENERIC_WORDS:
        if word in t:
            return cat
    for word, cat in _CATEGORY_WORDS:
        if word in t:
            return cat
    return 'other'


def retag(sections):
    """按当前关键词表重算章节类别。

    章节类别是入库时算好存下的，关键词表一改，存量数据的类别就停在旧口径上。
    比对两侧口径必须一致 —— 一边按新规则把「测评方案」排除，一边还拿旧分类
    当建设内容，比出来的分数就是错的。所以读的时候统一过一遍。
    """
    out = []
    for s in sections or []:
        item = dict(s)
        cat = classify(s.get('title', ''))
        item['category'] = cat
        item['category_label'] = CATEGORIES.get(cat, '其他')
        out.append(item)
    return out


def looks_like_heading(line: str) -> bool:
    """判断一行是否是章节标题。约束长度与结尾标点，避免把正文句子当标题。"""
    s = line.strip()
    if not s or len(s) > 40:
        return False
    if s.endswith(('。', '，', '；', '：', ';', ',', ':')) and len(s) > 12:
        return False
    if _CHAPTER_RE.match(s) or _CN_NUM_RE.match(s) or _PAREN_CN_RE.match(s):
        return True
    if _DOTTED_RE.match(s):
        # 1.1 建设内容   ← 标题
        # 1.1万元，其中…  ← 正文，用「点号后有无空白或非数字」区分
        return bool(re.match(r'^\d+(\.\d+){1,2}[\s、．.]', s)) or len(s) <= 24
    # 无编号但命中章节关键词的短行
    if len(s) <= 26 and not _HEADING_BAD.search(s) and any(w in s for w in _HEADING_WORDS):
        return True
    return False


# ---------------------------------------------------------------- 格式解析

def _dedupe_headers(pages):
    """跨页重复的首行/末行判为页眉页脚，剔掉。

    政务文档几乎每页都有「XX 项目可行性研究报告」和页码，
    不去掉会让每份报告的相似度凭空抬高。
    """
    from collections import Counter
    first, last = Counter(), Counter()
    for lines in pages:
        if lines:
            first[lines[0].strip()] += 1
            last[lines[-1].strip()] += 1
    n = len(pages)
    drop = set()
    for text, cnt in list(first.items()) + list(last.items()):
        if not text:
            continue
        if cnt >= max(2, n // 2) and len(text) <= 60:
            drop.add(text)
    out = []
    for lines in pages:
        kept = []
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s:
                continue
            if s in drop or _PAGE_NO_RE.match(s):
                continue
            kept.append(s)
        out.append(kept)
    return out


def parse_pdf(path: Path):
    import pdfplumber
    pages = []
    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            raw = page.extract_text() or ''
            lines = [ln for ln in raw.splitlines()]
            tables = []
            try:
                for t in page.extract_tables() or []:
                    rows = []
                    for row in t:
                        cells = [str(c).strip() for c in row if c and str(c).strip()]
                        if cells:
                            rows.append(' | '.join(cells))
                    if rows:
                        tables.append('\n'.join(rows))
            except Exception:
                pass
            pages.append({'lines': lines, 'tables': tables})
    cleaned = _dedupe_headers([p['lines'] for p in pages])
    chunks = []
    for p, lines in zip(pages, cleaned):
        if lines:
            chunks.append('\n'.join(lines))
        chunks.extend(p['tables'])
    return '\n\n'.join(chunks), page_count


def parse_docx(path: Path):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    blocks = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            txt = Paragraph(child, doc).text.strip()
            if txt:
                blocks.append(txt)
        elif tag == 'tbl':
            tbl = Table(child, doc)
            rows = []
            for row in tbl.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    rows.append(' | '.join(cells))
            if rows:
                blocks.append('\n'.join(rows))
    return '\n'.join(blocks), 0


def parse_txt(path: Path):
    for enc in ('utf-8', 'gb18030', 'utf-16'):
        try:
            return path.read_text(encoding=enc), 0
        except (UnicodeDecodeError, UnicodeError):
            continue
    return path.read_text(encoding='utf-8', errors='ignore'), 0


def parse_xlsx(path: Path):
    """Excel 取单元格文字：每张表按行拼成「A | B | C」，前面标表名。

    投资估算、预算明细、报价单基本都是这种形态。表格不做章节切分，也不进
    查重与功能抽取 —— 预算表的行标题天然相似，算进去只会把真重复淹掉。
    这里只要能把内容读出来给人看、能被检索到。
    """
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        blocks = []
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(v).strip() for v in row
                         if v is not None and str(v).strip()]
                if cells:
                    rows.append(' | '.join(cells))
            if rows:
                blocks.append('【{}】\n{}'.format(ws.title, '\n'.join(rows)))
        sheets = len(wb.worksheets)
    finally:
        wb.close()
    return '\n\n'.join(blocks), sheets


# 文本类与表格类分开：表格的合格门槛不能按「200 字」卡，一张预算表
# 往往只有几十个字加一堆数字，按正文字数算它永远是「解析失败」
SUPPORTED = {'.pdf', '.docx', '.txt', '.md', '.xlsx', '.xlsm'}
SHEET_EXT = {'.xlsx', '.xlsm'}
LEGACY = {'.doc', '.wps', '.et'}
SHEET_LEGACY = {'.xls', '.et'}


def parse_file(path: Path):
    """返回 (正文, 页数)。不支持的格式抛 ValueError，由调用方转成失败原因。"""
    ext = path.suffix.lower()
    if ext == '.pdf':
        return parse_pdf(path)
    if ext == '.docx':
        return parse_docx(path)
    if ext in ('.txt', '.md'):
        return parse_txt(path)
    if ext in SHEET_EXT:
        return parse_xlsx(path)
    if ext in SHEET_LEGACY:
        raise ValueError(f'暂不支持 {ext} 旧版表格，请用 Excel 另存为 .xlsx 后重新上传')
    if ext in LEGACY:
        raise ValueError(f'暂不支持 {ext} 旧格式，请用 Word 另存为 .docx 后重新上传')
    raise ValueError(f'不支持的文件类型 {ext}')


# ---------------------------------------------------------------- 章节切分

def heading_level(line: str) -> int:
    """标题层级：1=章（第X章 / 一、），2=节（1.1 / （一） / 无编号短标题）。

    为什么要分层：真可研一章下面挂七八个「1.1」「1.2」，
    把每个小节都当成独立章节去审，模型看到的全是两三百字的碎片，
    既看不出整章论证，也发现不了「同一功能在 3.2 和 3.6 各写一遍」。
    """
    s = line.strip()
    if _CHAPTER_RE.match(s) or _CN_NUM_RE.match(s):
        return 1
    return 2


def split_sections(text: str):
    """把正文切成 [{title, category, category_label, text, level}]。

    没有识别到任何标题时整体作为一节，不硬凑结构 —— 有些材料就是没有章节。
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    sections = []
    cur_title, cur_cat, cur_level, buf = '', 'other', 2, []

    def flush():
        body = '\n'.join(buf).strip()
        if body or cur_title:
            sections.append({
                'title': cur_title or '（正文）',
                'category': cur_cat,
                'category_label': CATEGORIES.get(cur_cat, '其他'),
                'text': body,
                'level': cur_level,
            })

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if looks_like_heading(s):
            flush()
            cur_title, cur_cat, cur_level, buf = s, classify(s), heading_level(s), []
        else:
            buf.append(s)
    flush()
    return [s for s in sections if len(s['text']) >= 20 or s['title']]


def split_chapters(text: str):
    """按一级标题把小节聚合成整章，供「整章内容审查」使用。

    返回结构与 split_sections 相同，只是粒度到章；没有一级标题的材料原样返回。
    小节标题保留在正文里（作为定位锚点），模型引用原文时能看出出自哪一节。
    """
    sections = split_sections(text)
    if not sections:
        return []
    if not any(s.get('level') == 1 for s in sections):
        return sections

    out = []
    for s in sections:
        if s.get('level') == 1 or not out:
            out.append({
                'title': s['title'],
                'category': s['category'],
                'category_label': s.get('category_label'),
                'text': s['text'],
                'level': 1,
                'parts': [s['title']] if s.get('level') != 1 else [],
            })
        else:
            cur = out[-1]
            piece = s['text']
            if s['title'] and s['title'] != '（正文）':
                piece = f'{s["title"]}\n{piece}'
                cur['parts'].append(s['title'])
            cur['text'] = (cur['text'] + '\n\n' + piece).strip()

    for c in out:
        c['chars'] = len(c['text'])
    return out



def clean_text(text: str) -> str:
    """统一空白：PDF 抽取会大量出现单字换行，先并成整段再处理。"""
    t = text.replace('\u3000', ' ').replace('\xa0', ' ')
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{2,}', '\n\n', t)
    return t.strip()
