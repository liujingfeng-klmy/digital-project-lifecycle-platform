"""从材料正文里抽取元数据：项目名称、建设单位、总投资、年度。

抽不全不报错 —— 这些字段导入后在页面上可手工改，抽取只是省点力气。
宁可留空，不能瞎猜：填错的元数据比空白更有害，因为比对时会被当真。
"""
import re

# 「可行性研究报告」「项目建议书」这类后缀要从项目名里去掉
_SUFFIX = (
    '可行性研究报告', '可行性研究', '项目建议书', '建设方案', '实施方案',
    '投资概算', '初步设计', '项目申请报告', '汇报材料', '汇报稿', '送审稿',
    '报批稿', '征求意见稿', '终稿', '定稿', '正式稿',
)
_NAME_PAT = [
    re.compile(r'项目名称[：:\s]+([^\n。；;，,]{4,60})'),
    re.compile(r'工程名称[：:\s]+([^\n。；;，,]{4,60})'),
    re.compile(r'系统名称[：:\s]+([^\n。；;，,]{4,60})'),
]
_UNIT_PAT = [
    re.compile(r'(?:建设单位|申报单位|项目单位|承建单位)[：:\s]+([^\n。；;，,]{2,60})'),
]
# 总投资：抓「总投资 860 万元」，也兼容「总投资约860.00万元」
_INVEST_PAT = [
    re.compile(r'总投资(?:估算|概算)?(?:金额)?(?:约|为|共)?[^\d\n]{0,6}([\d,]+(?:\.\d+)?)\s*万元'),
    re.compile(r'项目总投资[^\d\n]{0,10}([\d,]+(?:\.\d+)?)\s*万元'),
]
_YEAR_PAT = re.compile(r'(20[12]\d)\s*年')
# 带标签的日期才是立项年度。「申报日期：2023 年」在封面，「2019 年」在政策依据
# 与历史沿革里 —— 原来只取高频词，结果 36 个档案里 18 个年度被判成了引用年份
_YEAR_LABEL_PAT = re.compile(
    r'(?:申报|编制|立项|批复|评审|报批|送审|填报)(?:日期|时间|年度|年份)?'
    r'\s*[：:为]?\s*(20[12]\d)\s*年')
_YEAR_ANY_PAT = re.compile(r'(?<!\d)(20[12]\d)(?!\d)')


def year_from_filename(filename: str):
    """文件名里的年度：``xxx可研-2022（终版）``、``xxx_20231101``。

    只取第一个 20xx；文件名里出现年份，八成就是这一版的编制年度。
    """
    stem = re.sub(r'\.[A-Za-z0-9]{2,5}$', '', filename or '')
    m = _YEAR_ANY_PAT.search(stem)
    if not m:
        return None
    # 往前看一眼：紧挨数字的不算（流水号 12023001 之类）
    start = m.start()
    if start > 0 and stem[start - 1].isdigit():
        return None
    return int(m.group(1))


# 材料常写成「项目名称」单独一行、下一行才是「项目名称：XX」。正则跨行匹配时
# 会把第二行的标签一起吃进来，出来就是「项目名称：XX」这种带标签的名字
_NAME_LABEL = re.compile(r'^(?:项目名称|工程名称|系统名称|项目全称)\s*[：:]?\s*')


def _strip_label(name: str) -> str:
    prev = None
    s = (name or '').strip()
    while prev != s:
        prev = s
        s = _NAME_LABEL.sub('', s).strip(' 　:：·-—_')
    return s


def _strip_suffix(name: str) -> str:
    name = _strip_label(name)
    for s in sorted(_SUFFIX, key=len, reverse=True):
        if name.endswith(s):
            name = name[: -len(s)]
            break
    return re.sub(r'[（(【\[].*?[）)】\]]', '', name).strip(' _-—·')


def name_from_filename(filename: str) -> str:
    stem = re.sub(r'\.[A-Za-z0-9]{2,5}$', '', filename)
    stem = re.sub(r'^[\d\s._-]+', '', stem)  # 去掉开头的序号
    # 「05_某某系统_升级改造方案」→ 取第一段，后面那截是文档类型不是项目名
    if '_' in stem:
        stem = stem.split('_')[0]
    return _strip_suffix(stem)


def extract(text: str, filename: str = '', fallback_year=None):
    head = text[:6000]
    out = {
        'name': '',
        'unit': '',
        'investment': None,
        'year': None,
        'year_source': '',
        'name_source': '',
    }

    for pat in _NAME_PAT:
        m = pat.search(head)
        if m:
            out['name'] = _strip_suffix(m.group(1))
            out['name_source'] = '正文'
            break
    if not out['name']:
        cand = name_from_filename(filename)
        if len(cand) >= 4:
            out['name'] = cand
            out['name_source'] = '文件名'

    for pat in _UNIT_PAT:
        m = pat.search(head)
        if m:
            out['unit'] = m.group(1).strip()
            break

    for pat in _INVEST_PAT:
        m = pat.search(text)
        if m:
            try:
                val = float(m.group(1).replace(',', ''))
            except ValueError:
                continue
            # 低于 10 万或高于 100 亿的，多半是抓错了（比如「总投资 1 个系统」）
            if 10 <= val <= 1000000:
                out['investment'] = round(val, 2)
                break

    # 年度按可靠性排四档，先命中先用：
    #   导入时手填 > 封面「申报日期」> 文件名年份 > 正文高频年份
    # 最后一档只在没有更好线索时兜底，并且要求同一缩写出现两次以上 ——
    # 只出现一次的「2023 年」很可能是引用了一句文件标题
    if fallback_year:
        out['year'] = int(fallback_year)
        out['year_source'] = 'hint'
    if not out['year']:
        m = _YEAR_LABEL_PAT.search(head)
        if m:
            out['year'] = int(m.group(1))
            out['year_source'] = 'label'
    if not out['year']:
        fy = year_from_filename(filename)
        if fy:
            out['year'] = fy
            out['year_source'] = 'filename'
    if not out['year']:
        years = _YEAR_PAT.findall(text[:20000])
        if years:
            from collections import Counter
            y, cnt = Counter(years).most_common(1)[0]
            if cnt >= 2:
                out['year'] = int(y)
                out['year_source'] = 'frequency'

    return out
