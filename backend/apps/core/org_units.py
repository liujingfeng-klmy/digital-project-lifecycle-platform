"""申报单位名录的读取与归一。

两个用途：
1. 给前端出下拉选项（按口分组）；
2. 把可研报告里抽取到的单位名归一到名录标准写法 —— 材料上常写
   「XX市财政局」「市财政局」「财政局」，名录里只有一条，不归一会
   出现三个单位。

归一只做「确定能对上」的匹配：精确、别名、去行政区前缀、唯一包含。
对不上就原样返回并标记未匹配，由申报人在下拉里自己选 —— 猜错单位比留空更糟，
因为后面所有按单位的统计都跟着错。
"""
import re

from django.conf import settings

from .models import OrgUnit
from .org_data import GROUP_LABEL, GROUP_ORDER, LEGACY_ALIAS

# 行政区前缀由部署方在 config.json 的 org.prefixes 里配（如「XX市」「XX自治区」）。
# 未配置时按通用行政层级兜底，归一逻辑本身不变。
_PREFIX = tuple(
    p for p in (getattr(settings, 'ORG_PREFIXES', None) or ('市', '省', '自治区')) if p
)

# 材料里爱写全称，名录用规范简称。不做同义替换的话
# 「XX市人民代表大会常务委员会」永远对不上「市人大常委会」
_SYNONYM = (
    ('人民代表大会常务委员会', '人大常委会'),
    ('人民政府', '政府'),
    ('纪律检查委员会', '纪委'),
    ('监察委员会', '监委'),
)


def _core(name: str) -> str:
    """去掉行政区前缀、统一全称写法，用于宽松比对。"""
    s = re.sub(r'\s+', '', name or '')
    for p in _PREFIX:
        if s.startswith(p):
            s = s[len(p):]
            break
    for full, short in _SYNONYM:
        s = s.replace(full, short)
    if s.startswith('市') and len(s) > 2:
        s = s[1:]
    return s


def list_grouped(include_inactive=False, only_groups=None):
    """返回 [{key, label, items:[{id, name, subgroup}]}]，空组不出现。"""
    qs = OrgUnit.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    if only_groups:
        qs = qs.filter(group__in=only_groups)

    buckets = {}
    for u in qs:
        buckets.setdefault(u.group, []).append({
            'id': u.id, 'name': u.name, 'subgroup': u.subgroup,
        })
    out = []
    for key in GROUP_ORDER:
        if buckets.get(key):
            out.append({'key': key, 'label': GROUP_LABEL.get(key, key),
                        'items': buckets[key]})
    # 名录里出现未登记的分组值时不丢数据，补在末尾
    for key, items in buckets.items():
        if key not in GROUP_ORDER:
            out.append({'key': key, 'label': key, 'items': items})
    return out


def names(include_inactive=True):
    qs = OrgUnit.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    return set(qs.values_list('name', flat=True))


def normalize(raw, allow_inactive=True):
    """把任意外部写法归一为名录标准名。

    返回 (标准名, 是否命中)。未命中时原样返回清理后的字符串。
    """
    s = re.sub(r'\s+', '', raw or '')
    if not s:
        return '', False

    qs = OrgUnit.objects.all()
    if not allow_inactive:
        qs = qs.filter(active=True)
    pool = list(qs.values_list('name', 'aliases'))

    # 1) 精确
    for name, _aliases in pool:
        if s == name:
            return name, True

    # 2) 别名（库内）与历史别名表
    for name, aliases in pool:
        for a in (aliases or []):
            if re.sub(r'\s+', '', a) == s:
                return name, True
    if s in LEGACY_ALIAS:
        target = LEGACY_ALIAS[s]
        if any(n == target for n, _ in pool):
            return target, True

    # 3) 去前缀后精确
    core = _core(s)
    if core:
        hits = [n for n, _ in pool if _core(n) == core]
        if len(hits) == 1:
            return hits[0], True

    # 4) 唯一包含（材料里常写成「XX市XX局（本级）」这类）
    if len(core) >= 3:
        hits = [n for n, _ in pool if _core(n) and (_core(n) in core or core in _core(n))]
        if len(hits) == 1:
            return hits[0], True

    return s, False


def is_known(raw) -> bool:
    return normalize(raw)[1]
