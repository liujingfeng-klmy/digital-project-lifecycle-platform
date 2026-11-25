"""申报材料预解析：先传材料，再填表。

原来的顺序是「先手填名称、单位、投资，再上传可研」，两个后果：一是同一个人
两处写的数字对不上（表单 198 万、报告 189 万），二是单位名称各写各的。
改成先传材料，把能认出来的字段直接填进表单，申报人只做确认和补空。

抽不全是常态，不报错。缺哪些字段、去模板哪一节补，一并返回给前端展示 ——
这是「让他们按模板改材料」的抓手，否则一句「解析失败」用户不知道改哪里。

抽取分工：
- 规则抽取负责确定性字段（名称、单位、投资、年度、建设期）。这些字段在可研
  里位置固定、写法固定，正则比模型可靠，也不会把 198 万读成 189 万。
- 模型只在规则没抽到时兜底，且只填不覆盖。
"""
import json
import logging
import re
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.archive import extract as archive_extract
from apps.archive import textparse
from apps.core import org_units

log = logging.getLogger(__name__)

# 与前端「业务领域」下拉保持同一套取值
CATEGORIES = ['自然资源', '政务服务', '医疗保障', '教育', '公安政法', '交通运输',
              '生态环境', '城市管理', '农业农村', '文化旅游', '应急管理', '其他']

FIELD_LABEL = {
    'name': '项目名称',
    'unit': '申报单位',
    'investment': '总投资',
    'year': '申报年度',
    'duration_months': '建设周期',
    'summary': '建设内容',
    'necessity': '建设必要性',
    'basis': '政策依据',
    'category': '业务领域',
}

# 必填项：缺了这些，材料基本没法进预审。其余字段缺了只是信息不全
REQUIRED_FIELDS = ['name', 'unit', 'investment', 'summary']

# 缺失时告诉用户去模板哪一节补，比一句「识别失败」有用
FIELD_HINT = {
    'name': '封面标题，或第 1.1 节「项目名称」',
    'unit': '封面「报告编制单位」，或第 1.2.1 节',
    'investment': '第 8 章「投资估算和资金来源」的投资合计，或第 1.4 节',
    'year': '封面「报告编制日期」，或第 1 节申报年度',
    'duration_months': '第 7 章「项目实施计划」中的建设期',
    'summary': '第 1.3.4 节「项目建设内容」，需 30 字以上',
    'necessity': '第 1.3.1 节「建设背景」，或第 2 章「需求分析及建设的必要性」',
    'basis': '第 1.2.2 节「编制依据」中的政策法规清单',
    'category': '第 1.3.4 节「项目建设内容」',
}

FIELD_ORDER = ['name', 'unit', 'investment', 'year', 'duration_months',
               'summary', 'necessity', 'basis', 'category']

STAGE_TTL_HOURS = 24
_MAX_TEXT = 800_000


# ---------------------------------------------------------------- 暂存区
def _stage_root() -> Path:
    return Path(settings.MEDIA_ROOT) / '_prefill'


def purge_expired():
    """清掉超过 24 小时没被使用的暂存材料。建项目失败或用户放弃时留下的。"""
    root = _stage_root()
    if not root.exists():
        return 0
    deadline = timezone.now() - timedelta(hours=STAGE_TTL_HOURS)
    removed = 0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        try:
            mtime = timezone.datetime.fromtimestamp(d.stat().st_mtime,
                                                   tz=timezone.get_current_timezone())
        except OSError:
            continue
        if mtime < deadline:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def stage_file(uploaded):
    """把上传文件落到暂存目录，返回 (token, 路径, 文件名)。"""
    purge_expired()
    token = uuid.uuid4().hex
    folder = _stage_root() / token
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(uploaded.name or 'material').name
    path = folder / ('source' + Path(name).suffix.lower())
    with open(path, 'wb') as fh:
        for chunk in uploaded.chunks():
            fh.write(chunk)
    (folder / 'meta.json').write_text(json.dumps({
        'filename': name,
        'size': uploaded.size,
        'created': timezone.now().isoformat(),
    }, ensure_ascii=False), encoding='utf-8')
    return token, path, name


def load_stage(token):
    """取暂存材料。返回 (路径, 文件名) 或 (None, '')。"""
    if not token or not re.fullmatch(r'[0-9a-f]{32}', token or ''):
        return None, ''
    folder = _stage_root() / token
    meta_path = folder / 'meta.json'
    if not meta_path.exists():
        return None, ''
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, ''
    created = meta.get('created')
    if created:
        try:
            if timezone.datetime.fromisoformat(created) < \
                    timezone.now() - timedelta(hours=STAGE_TTL_HOURS):
                return None, ''
        except ValueError:
            pass
    for p in folder.iterdir():
        if p.name.startswith('source') and p.is_file():
            return p, meta.get('filename') or p.name
    return None, ''


def drop_stage(token):
    if not token or not re.fullmatch(r'[0-9a-f]{32}', token or ''):
        return
    shutil.rmtree(_stage_root() / token, ignore_errors=True)


# ---------------------------------------------------------------- 规则抽取
_DURATION_PAT = [
    re.compile(r'(?:建设期|建设周期|项目工期|总工期|工期|实施周期|建设时间)'
               r'[^\d\n]{0,10}(\d{1,3})\s*(个月|月)'),
    re.compile(r'(?:建设期|建设周期|项目工期|总工期|工期|实施周期|建设时间)'
               r'[^\d\n]{0,10}(\d{1,2})\s*年'),
]
_DATE_PAT = re.compile(r'(?:编制日期|报告日期|编制时间|日期)[：:\s]*'
                       r'(20[12]\d)\s*年')
_BOOK_RE = re.compile(r'《[^》]{4,80}》')
_BASIS_TITLE_WORDS = ('办法', '规划', '通知', '意见', '方案', '纲要', '条例',
                      '规定', '标准', '规范', '指南')

_CATEGORY_WORDS = {
    '自然资源': ('不动产', '国土', '规划一张图', '地籍', '测绘', '土地', '林草', '自然资源'),
    '政务服务': ('政务服务', '一网通办', '审批', '便民', '政务大厅', '一件事', '12345'),
    '医疗保障': ('医保', '医疗', '医院', '卫生健康', '公共卫生', '病历', '门诊'),
    '教育': ('教育', '学校', '校园', '学生', '教学', '招生', '课堂'),
    # 注意别用「治安」这种两字词：社会治理、综合治理都会命中，
    # 一个城市治理类项目会被判成公安政法
    '公安政法': ('公安', '警务', '社会治安', '治安管理', '政法', '法院', '检察',
                 '司法', '戒毒', '信访'),
    '交通运输': ('交通', '公路', '公交', '运输', '路网', '停车', '治超'),
    '生态环境': ('生态环境', '环保', '污染', '监测', '排污', '碳排'),
    '城市管理': ('城市管理', '城管', '市政', '环卫', '园林', '供水', '燃气', '供热',
                 '一网统管', '城市治理', '社会治理', '网格'),
    '农业农村': ('农业', '农村', '乡村', '农机', '畜牧', '种植'),
    '文化旅游': ('文化', '旅游', '广电', '体育', '文物', '非遗', '图书馆'),
    '应急管理': ('应急', '防灾', '救灾', '消防', '安全生产', '预警'),
}


_NAME_LABEL_RE = re.compile(r'^(?:项目名称|工程名称|系统名称|项目全称)\s*[：:]?\s*')

# 材料里有大量没被识别成标题的短行（「建设紧迫性」「建设必要性」「项目背景」），
# split_sections 会把它们并进上一节正文。留在字段里就是一句莫名其妙的尾巴
_HEADING_WORDS = (
    '必要性', '紧迫性', '背景', '意义', '目标', '内容', '依据', '原则', '范围',
    '期限', '投资', '估算', '概算', '来源', '风险', '保障', '效益', '结论',
    '建议', '组织', '进度', '计划', '方案', '规模', '需求', '现状', '问题',
    '措施', '管理', '安全', '运维', '培训', '评价', '实施', '概述', '简介',
)
_SENTENCE_PUNCT = '。；;，,、：:！!？?“”"'


def _is_stray_heading(line):
    """判断孤立短行是不是标题，而不是正文句子。

    真句子一般有标点或够长；标题是「建设紧迫性」这种 2–20 字、无标点、
    含标题词的孤行。宁可少删——把正文句子删了比留个标题更糟。
    """
    s = (line or '').strip()
    if not 2 <= len(s) <= 20:
        return False
    if any(ch in s for ch in _SENTENCE_PUNCT):
        return False
    return any(w in s for w in _HEADING_WORDS)


def _strip_edge_headings(body):
    lines = (body or '').splitlines()
    while lines and _is_stray_heading(lines[-1]):
        lines.pop()
    while lines and _is_stray_heading(lines[0]):
        lines.pop(0)
    return '\n'.join(lines).strip()


def _strip_name_label(name):
    prev = None
    s = (name or '').strip()
    while prev != s:
        prev = s
        s = _NAME_LABEL_RE.sub('', s).strip(' 　:：·-—_')
    return s


def _clean(text, limit):
    """去掉页眉页脚残留与多余空白，截到 limit 字以内，尽量在句号处收尾。"""
    s = re.sub(r'[ \t\u3000]+', ' ', text or '')
    s = re.sub(r'\n{2,}', '\n', s).strip()
    s = _strip_edge_headings(s)
    if len(s) <= limit:
        return s
    cut = s[:limit]
    for mark in ('。', '；', '\n'):
        idx = cut.rfind(mark)
        if idx >= limit * 0.6:
            return _strip_edge_headings(cut[:idx + 1].strip())
    return _strip_edge_headings(cut.strip())


def _section_by(sections, *words, exclude=(), min_len=20):
    """按标题关键词找小节，返回第一个命中且正文够长的小节。"""
    for s in sections or []:
        title = s.get('title') or ''
        if any(w in title for w in words) and not any(e in title for e in exclude):
            if len((s.get('text') or '').strip()) >= min_len:
                return s
    return None


def _section_after(sections, idx, max_follow=2, min_len=30):
    """取某节之后的连续正文。

    可研模板常把「编制依据」写成只有标题、内容在下一个「（一）政策法规」节里，
    按标题找会漏掉。向后看几节，遇到下一个章标题就停。
    """
    out = []
    for s in (sections or [])[idx + 1: idx + 1 + max_follow]:
        title = s.get('title') or ''
        if re.search(r'^第[一二三四五六七八九十]+章', title):
            break
        body = (s.get('text') or '').strip()
        if len(body) >= min_len:
            out.append(body)
    return '\n'.join(out)


def _first_line(text):
    for ln in (text or '').splitlines():
        s = ln.strip()
        if s:
            return s
    return ''


def _guess_name(text, sections, filename):
    """项目名称：先看「项目名称」小节，再看正文标注，最后从文件名退。"""
    sec = _section_by(sections, '项目名称', min_len=2)
    if sec:
        cand = _strip_name_label(_first_line(sec['text']))
        cand = re.sub(r'^[（(【\[]?[一二三四五六七八九十\d.、]*[）)】\]]?', '', cand)
        cand = _strip_name_label(cand.strip(' 　:：'))
        if 4 <= len(cand) <= 100 and not re.search(r'[Xx×]{3,}', cand):
            return cand, '第 1.1 节'
    meta = archive_extract.extract(text[:12000], '')
    cand = _strip_name_label(meta.get('name') or '')
    if 4 <= len(cand) <= 100 and not re.search(r'[Xx×]{3,}', cand):
        return cand, '正文标注'
    cand = archive_extract.name_from_filename(filename or '')
    if 4 <= len(cand) <= 100 and not re.search(r'[Xx×]{3,}', cand):
        return cand, '文件名'
    return '', ''


def _guess_unit(text, sections):
    sec = _section_by(sections, '编制单位', min_len=2)
    if sec:
        cand = _first_line(sec['text'])
        if 2 <= len(cand) <= 60:
            return cand, '第 1.2.1 节'
    m = re.search(r'(?:报告编制单位|编制单位|建设单位|申报单位|项目单位)[：:\s]+'
                  r'([^\n。；;，,]{2,60})', text[:8000])
    if m:
        return m.group(1).strip(), '封面'
    meta = archive_extract.extract(text[:12000], '')
    if meta.get('unit'):
        return meta['unit'], '正文'
    return '', ''


def _guess_investment(text, sections):
    meta = archive_extract.extract(text[:40000], '')
    if meta.get('investment'):
        return meta['investment'], '正文'
    # 「总投资」在正文里没写时，投资估算章常有「合计 198.00 万元」
    sec = _section_by(sections, '投资估算', '投资概算', '总投资')
    if sec:
        m = re.search(r'(?:合计|总计|共计)[^\d\n]{0,6}([\d,]+(?:\.\d+)?)\s*万元',
                      sec['text'])
        if m:
            try:
                val = float(m.group(1).replace(',', ''))
                if 10 <= val <= 1_000_000:
                    return round(val, 2), '投资估算章'
            except ValueError:
                pass
    return None, ''


def _guess_year(text, sections):
    m = _DATE_PAT.search(text[:6000])
    if m:
        return int(m.group(1)), '封面'
    sec = _section_by(sections, '编制日期', '报告日期')
    if sec:
        m = re.search(r'(20[12]\d)', sec['text'])
        if m:
            return int(m.group(1)), '封面'
    return None, ''


def _guess_duration(text, sections):
    for pat in _DURATION_PAT:
        m = pat.search(text)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            months = n * 12 if unit == '年' else n
            if 1 <= months <= 120:
                return months, '正文'
    sec = _section_by(sections, '实施计划', '实施进度', '建设周期', '工期')
    if sec:
        for pat in _DURATION_PAT:
            m = pat.search(sec['text'])
            if m:
                n = int(m.group(1))
                months = n * 12 if m.group(2) == '年' else n
                if 1 <= months <= 120:
                    return months, '实施计划章'
    return None, ''


def _guess_summary(sections):
    sec = _section_by(sections, '建设内容', exclude=('建设内容及规模测算',))
    if not sec:
        sec = _section_by(sections, '主要建设内容', '建设规模', '主要任务')
    if sec:
        body = _clean(sec['text'], 1200)
        if len(body) >= 30:
            return body, sec['title'][:14]
    return '', ''


def _guess_necessity(sections):
    sec = _section_by(sections, '必要性', exclude=('建设必要性分析结论',))
    if not sec:
        sec = _section_by(sections, '建设背景', '项目背景')
    if sec:
        body = _clean(sec['text'], 800)
        if len(body) >= 20:
            return body, sec['title'][:14]
    return '', ''


def _guess_basis(text, sections):
    """政策依据：只认政策法规清单。

    可研里有两个「编制依据」——第 1.2.2 节是政策法规，8.1.1 节是投资估算的
    计价依据（人月单价、等保测评取费）。抓错那个，政策依据栏里就全是费率
    公式。所以先找「政策法规」小节，再找第 1 章的编制依据，并排除投资估算类。
    """
    sections = sections or []
    books = []
    sec = _section_by(sections, '政策法规', '法律法规')
    if not sec:
        for i, s in enumerate(sections):
            title = s.get('title') or ''
            if ('编制依据' in title or '政策依据' in title or '建设依据' in title) \
                    and not any(w in title for w in ('投资估算', '价格', '取费', '方法')):
                body = (s.get('text') or '').strip()
                if len(body) < 30:
                    body = _section_after(sections, i)
                sec = {'title': title, 'text': body}
                break
    if sec:
        books = _BOOK_RE.findall(sec['text'])
    if not books:
        # 没有专门小节时，从正文开头找带政策语感的文件名
        for b in _BOOK_RE.findall(text[:30000]):
            if any(w in b for w in _BASIS_TITLE_WORDS):
                books.append(b)
    seen, out = set(), []
    for b in books:
        if b not in seen:
            seen.add(b)
            out.append(b)
    if out:
        return '；'.join(out[:6])[:500], '编制依据章' if sec else '正文'
    return '', ''


def _guess_category(summary, necessity, name):
    """判业务领域。名称里的词权重更高，单个词的计数封顶。

    不封顶的话，一个项目只要某个场景反复出现（比如「一网统管」里信访提了 8 次），
    整项目的领域就被那一类吃掉。名称是申报人自己定的，最能说明这是什么项目。
    """
    name_blob = name or ''
    body_blob = f'{summary} {necessity}'
    scores = {}
    for k, words in _CATEGORY_WORDS.items():
        total = 0
        for w in words:
            total += min(name_blob.count(w), 2) * 3 + min(body_blob.count(w), 4)
        scores[k] = total
    best = max(scores, key=scores.get) if scores else '其他'
    return (best if scores.get(best, 0) > 0 else '其他'), '按建设内容判断'


def rule_extract(text, sections, filename):
    fields, sources = {}, {}
    for key, fn in (
        ('name', lambda: _guess_name(text, sections, filename)),
        ('unit', lambda: _guess_unit(text, sections)),
        ('investment', lambda: _guess_investment(text, sections)),
        ('year', lambda: _guess_year(text, sections)),
        ('duration_months', lambda: _guess_duration(text, sections)),
        ('summary', lambda: _guess_summary(sections)),
        ('necessity', lambda: _guess_necessity(sections)),
        ('basis', lambda: _guess_basis(text, sections)),
    ):
        try:
            val, src = fn()
        except Exception as exc:  # 单字段抽取出错不影响其它字段
            log.info('抽取 %s 失败：%s', key, exc)
            val, src = (None if key in ('investment', 'year', 'duration_months') else ''), ''
        if val not in (None, ''):
            fields[key] = val
            sources[key] = src
    cat, csrc = _guess_category(fields.get('summary', ''),
                               fields.get('necessity', ''), fields.get('name', ''))
    if cat and cat != '其他':
        fields['category'] = cat
        sources['category'] = csrc
    return fields, sources


# ---------------------------------------------------------------- AI 兜底
_AI_SYS = (
    '你是政务信息化项目申报材料的字段抽取助手。只输出 JSON 对象，不要解释文字，'
    '不要代码围栏。字段含义：name 项目名称；unit 建设单位全称；'
    'investment 总投资（万元，只要数字）；duration_months 建设期（月，整数）；'
    'summary 建设内容（概括到 300 字以内，写建什么系统、覆盖什么业务）；'
    'necessity 建设必要性（200 字以内）；basis 政策依据（只列文件名称，用分号分隔）；'
    'category 业务领域，只能从这些里选：' + '、'.join(CATEGORIES) + '。'
    '材料里没有的信息填空字符串或 null，绝对不要编造。'
)


def ai_extract(text, sections, wanted, sensitive=False):
    """只抽规则没抽到的字段。模型不可用时返回空 dict，不阻断主流程。

    sensitive=True 或全局 sensitive.skip_ai 打开时，绝不把材料正文外发到
    模型服务——预解析发生在项目创建之前，此时还没有项目级 sensitive 标记，
    只能靠申报人勾选（前端传入）与全局策略兜底。
    """
    from apps.aicore import llm

    import os
    full = llm.load_config()
    if sensitive and (full.get('sensitive') or {}).get('skip_ai', True):
        return {}, '涉敏材料不外发，仅用规则识别'
    # 预解析发生在项目创建之前，此时拿不到项目级涉敏标记，正文一旦外发
    # 就无法撤回。默认禁用预解析阶段的 AI 抽取，需显式置 PM_PREFILL_AI=1
    # 且确认允许出网才启用；正式预审的 AI 审查仍按项目 sensitive 标记控制。
    if os.environ.get('PM_PREFILL_AI', '0') != '1':
        return {}, '预解析默认不外发，仅用规则识别'
    cfg = full.get('llm', {})
    if not (cfg.get('enabled') and cfg.get('base_url') and cfg.get('model')):
        return {}, '未启用模型，仅用规则识别'

    heads = [s.get('title', '') for s in (sections or [])[:40] if s.get('title')]
    parts = ['【章节标题】', ' / '.join(heads), '',
             '【材料开头】', _clean(text[:9000], 6000)]
    for word in ('建设内容', '必要性', '编制依据', '投资估算'):
        sec = _section_by(sections, word)
        if sec:
            parts += ['', f'【{sec["title"]}】', _clean(sec['text'], 1500)]
    parts += ['', f'【需要抽取的字段】{"、".join(wanted)}']

    try:
        content, _usage = llm.chat(
            [{'role': 'system', 'content': _AI_SYS},
             {'role': 'user', 'content': '\n'.join(parts)}],
            temperature=0.0, max_tokens=1200, timeout=45)
    except Exception as exc:
        log.warning('AI 抽取失败：%s', exc)
        return {}, f'模型调用失败：{exc}'

    data = _parse_json_obj(content)
    if not data:
        return {}, '模型返回内容无法解析'
    out = {}
    for k in wanted:
        v = data.get(k)
        if v in (None, '', []):
            continue
        if k in ('investment', 'duration_months', 'year'):
            try:
                num = float(re.sub(r'[^\d.]', '', str(v)) or 0)
            except ValueError:
                continue
            if k == 'investment' and not (10 <= num <= 1_000_000):
                continue
            if k == 'duration_months' and not (1 <= num <= 120):
                continue
            if k == 'year' and not (2000 <= num <= 2100):
                continue
            out[k] = round(num, 2) if k == 'investment' else int(num)
        elif k == 'category':
            if str(v) in CATEGORIES:
                out[k] = str(v)
        else:
            s = str(v).strip()
            if len(s) >= (30 if k == 'summary' else 4):
                out[k] = s[:1200]
    return out, ''


def _parse_json_obj(content):
    s = (content or '').strip()
    s = re.sub(r'^```(?:json)?|```$', '', s, flags=re.M).strip()
    start, end = s.find('{'), s.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        val = json.loads(s[start:end + 1])
        return val if isinstance(val, dict) else None
    except ValueError:
        return None


# ---------------------------------------------------------------- 汇总
def analyze(path, filename, sensitive=False):
    """解析一份材料并抽取字段。返回给前端的结果字典（不含正文）。"""
    raw, pages = textparse.parse_file(Path(path))
    text = textparse.clean_text(raw)
    if len(text) < 200:
        raise ValueError(
            f'只解析出 {len(text)} 个字符，可能是扫描件（没有文字层）。'
            '请用带文字层的 PDF，或先做 OCR 再上传')

    sections = textparse.split_sections(text)
    for s in sections:
        s['chars'] = len(s['text'])

    fields, sources = rule_extract(text, sections, filename)

    # 单位归一到名录；对不上就保留原文，前端提示手动选
    unit_raw = fields.get('unit', '')
    unit_unmatched = False
    if unit_raw:
        std, ok = org_units.normalize(unit_raw)
        fields['unit'] = std
        unit_unmatched = not ok
        if ok and std != unit_raw:
            sources['unit'] = f'{sources.get("unit") or "正文"}·已归一'

    missing = [k for k in FIELD_ORDER if k not in fields]
    ai_note = ''
    ai_used = False
    ai_fields = []
    wanted = [k for k in missing if k in ('summary', 'necessity', 'basis', 'category')]
    if wanted:
        extra, ai_note = ai_extract(text[:20000], sections, wanted,
                                    sensitive=sensitive)
        if extra:
            ai_used = True
            for k, v in extra.items():
                fields[k] = v
                # 模型给的是概括，不是原文摘录 —— 必须让申报人核对，
                # 不能和「材料里明写着」的字段用同一种标记
                sources[k] = '模型概括，请核对原文'
                ai_fields.append(k)
            missing = [k for k in FIELD_ORDER if k not in fields]

    return {
        'filename': filename,
        'char_count': len(text),
        'page_count': pages or 0,
        'section_count': len(sections),
        'headings': [s['title'] for s in sections[:30]],
        'fields': fields,
        'sources': sources,
        'ai_fields': ai_fields,
        'missing': [{'key': k, 'label': FIELD_LABEL[k], 'hint': FIELD_HINT[k]}
                    for k in FIELD_ORDER if k not in fields],
        'missing_required': [k for k in REQUIRED_FIELDS if k not in fields],
        'unit_unmatched': unit_unmatched,
        'unit_raw': unit_raw,
        'ai_used': ai_used,
        'ai_note': ai_note,
        'category_options': CATEGORIES,
    }
