"""重复建设比对引擎。

为什么不能只算一个整篇相似度：
  政务可研报告是模板化写作的，两篇毫不相干的报告也能有 40% 的整篇相似度
  ——「项目建设背景」「加强组织领导」这类话术人人都在写。整篇分数一高就
  报警，等于天天报警，没人会看。

所以分三层看：
  1. 整篇层   TF 余弦 + 包含度（识别整篇照抄），去掉套话后计算
  2. 章节层   同类章节对齐后逐章比 —— 真正有判别力的是「建设内容」和
              「投资估算」，这两章相似度高了才叫重复建设
  3. 片段层   段落级 Jaccard，用倒排索引加速，给出可对照的原文
最终分数三层加权，理由逐条写出来，能指着原文说话。
"""
import hashlib
import math
import re
from collections import Counter, defaultdict

from .textparse import CATEGORIES, COMPARE_CATEGORIES, retag

# 套话词组：几乎每份可研都有，留着会淹没真正的内容信号
FILLER = {
    '推进', '切实', '不断', '进一步', '有效', '充分', '积极', '全面', '认真',
    '严格', '做好', '开展', '加强', '完善', '提升', '同时', '目前', '主要',
    '方面', '情况', '相关', '根据', '按照', '要求', '通过', '进行', '以及',
    '能够', '可以', '需要', '提供', '支持', '实现', '工作', '我们', '有关',
    '结合', '充分', '形成', '建立', '健全', '推动', '促进', '确保', '保障',
    '发挥', '着力', '坚持', '围绕', '突出', '注重', '有利', '具有', '作为',
    '对于', '由于', '因此', '但是', '并且', '或者', '如果', '其中', '之一',
    '一方', '一定', '各项', '各自', '上述', '如下', '如下', '上述', '本项',
}

_PUNCT = re.compile(r'[\s\u3000，。、；：？！“”‘’（）《》〈〉【】\[\]{}—…·,.;:?!()\'"/\\|<>＋+=*&^%$#@~`_]')
_FULL2HALF = {ord(c): ord(h) for c, h in zip('０１２３４５６７８９．，％', '0123456789.,%')}


def normalize(text: str) -> str:
    """只留汉字与数字，数字做格式归一。标点被去掉，避免排版差异影响比对。"""
    t = text.translate(_FULL2HALF)
    t = _PUNCT.sub('', t)
    t = re.sub(r'(?<=\d),(?=\d{3}\b)', '', t)
    t = re.sub(r'(\d+)\.0+(?!\d)', r'\1', t)
    return t


def build_grams(text: str, top: int = 2000, keep_filler: bool = False) -> dict:
    """中文二字组词频。二字组不依赖分词库，对中文短文本足够稳。"""
    s = normalize(text)
    if len(s) < 2:
        return {}
    cnt = Counter()
    for i in range(len(s) - 1):
        g = s[i:i + 2]
        if not keep_filler and g in FILLER:
            continue
        cnt[g] += 1
    if not cnt:
        return {}
    # 只留最高频的一段，控制数据库体积；过低的（出现 1 次）基本是错别字与噪声
    if len(cnt) > top:
        cnt = Counter(dict(cnt.most_common(top)))
    return dict(cnt)


def _l2(vec: dict) -> float:
    return math.sqrt(sum(v * v for v in vec.values())) or 1.0


def cosine(a: dict, b: dict, a_norm: float = None, b_norm: float = None) -> float:
    if not a or not b:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    inter = 0.0
    for k, v in small.items():
        w = big.get(k)
        if w:
            inter += v * w
    if not inter:
        return 0.0
    return inter / ((a_norm or _l2(a)) * (b_norm or _l2(b)))


def containment(a: dict, b: dict) -> float:
    """包含度：短的那篇有多大比例出现在长的里面。整篇照抄会被这一项抓住。"""
    if not a or not b:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    hit = sum(1 for k in small if k in big)
    return hit / len(small)


def gram_set(grams: dict) -> set:
    return set(grams.keys())


def text_fingerprint(text: str) -> str:
    """按正文认人，不看文件。

    同一份报告换个格式另存、或改了几个错别字，二进制指纹就变了，
    但正文还是一样的 —— 拿它去比对会跟自己撞出 100%。
    """
    return hashlib.sha256(normalize(text).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------- 段落

def split_paragraphs(sections, min_len: int = 45, max_len: int = 320, limit: int = 500,
                     categories=None):
    """把章节正文切成可比对的段落块。

    PDF 抽取出来的正文常常一整章只有一行，所以先按句号切句再按长度归并，
    不能直接按换行切。

    categories 给了就只切这几类章：原文对照只应出现在建设内容里，
    把「加强组织领导」这类段落摆出来对照，除了一张两张几乎一样的图，什么也证明不了。
    """
    out = []
    for sec in retag(sections):
        if categories and sec.get('category', 'other') not in categories:
            continue
        title = sec.get('title', '')
        cat = sec.get('category', 'other')
        body = sec.get('text', '')
        buf = ''
        for sent in re.split(r'(?<=[。！？；])', body):
            sent = sent.strip()
            if not sent:
                continue
            if buf and len(buf) + len(sent) > max_len:
                if len(buf) >= min_len:
                    out.append({'section': title, 'category': cat, 'text': buf})
                buf = sent
            else:
                buf += sent
            if len(buf) >= max_len:
                if len(buf) >= min_len:
                    out.append({'section': title, 'category': cat, 'text': buf})
                buf = ''
        if len(buf) >= min_len:
            out.append({'section': title, 'category': cat, 'text': buf})
        elif buf and out and out[-1]['section'] == title:
            out[-1]['text'] += buf
    return out[:limit]


def find_snippets(q_paras, q_sets, d_paras, d_sets, top: int = 6,
                  min_score: float = 0.42):
    """段落级相似片段。倒排索引加速：先按 gram 命中数取候选，再算 Jaccard。"""
    if not q_paras or not d_paras:
        return []
    index = defaultdict(list)
    for i, gs in enumerate(d_sets):
        for g in gs:
            index[g].append(i)

    pairs = []
    for qi, qgs in enumerate(q_sets):
        if len(qgs) < 8:
            continue
        cnt = Counter()
        for g in qgs:
            for di in index.get(g, ()):
                cnt[di] += 1
        if not cnt:
            continue
        need = max(4, int(len(qgs) * min_score))
        for di, hit in cnt.most_common(8):
            if hit < need:
                break
            dgs = d_sets[di]
            union = len(qgs) + len(dgs) - hit
            score = hit / union if union else 0.0
            if score >= min_score:
                pairs.append({
                    'score': round(score, 3),
                    'query_text': q_paras[qi]['text'],
                    'query_section': q_paras[qi]['section'],
                    'hit_text': d_paras[di]['text'],
                    'hit_section': d_paras[di]['section'],
                })
    pairs.sort(key=lambda p: -p['score'])
    # 同一段不要重复出现多次
    seen, out = set(), []
    for p in pairs:
        key = (p['query_text'][:40], p['hit_text'][:40])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= top:
            break
    return out


# ---------------------------------------------------------------- 章节与元数据

SECTION_WEIGHT = {
    'plan': 1.0,      # 建设内容与方案 —— 判断重复建设最关键的章节
    'demand': 0.9,
    'invest': 0.85,
    'data': 0.6,
    'tech': 0.5,
    'security': 0.4,
    'ops': 0.35,
    'schedule': 0.3,
    'perf': 0.25,
    'necessity': 0.2,  # 必要性最像，最不能说明问题
    'risk': 0.15,
    'other': 0.2,
}


def compare_sections(q_sections, d_sections, categories=None):
    """同类章节对齐后逐章比。返回 [{category, label, score, chars, weight}]

    categories 不给就比全部；给了就只比这几类 —— 查重只比建设内容，
    其余章节的相似度进报告只会误导人。
    """
    qmap, dmap = defaultdict(list), defaultdict(list)
    for s in retag(q_sections):
        qmap[s.get('category', 'other')].append(s)
    for s in retag(d_sections):
        dmap[s.get('category', 'other')].append(s)

    hits = []
    for cat, qlist in qmap.items():
        if categories and cat not in categories:
            continue
        dlist = dmap.get(cat)
        if not dlist:
            continue
        qtext = '\n'.join(s.get('text', '') for s in qlist)
        dtext = '\n'.join(s.get('text', '') for s in dlist)
        if len(normalize(qtext)) < 60 or len(normalize(dtext)) < 60:
            continue
        qg, dg = build_grams(qtext), build_grams(dtext)
        score = 0.6 * cosine(qg, dg) + 0.4 * containment(qg, dg)
        hits.append({
            'category': cat,
            'label': CATEGORIES.get(cat, '其他'),
            'score': round(score, 3),
            'chars': min(len(qtext), len(dtext)),
            'weight': SECTION_WEIGHT.get(cat, 0.2),
            'query_chars': len(qtext),
            'hit_chars': len(dtext),
        })
    hits.sort(key=lambda h: -h['score'] * h['weight'])
    return hits


def name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ga, gb = build_grams(a, keep_filler=True), build_grams(b, keep_filler=True)
    if not ga or not gb:
        return 0.0
    cos = cosine(ga, gb)
    cont = containment(ga, gb)
    return round(max(cos, cont * 0.95), 3)


def meta_score(q, d):
    """元数据相似度：名称最重，其次投资规模，再次单位、年度。"""
    ns = name_similarity(q.get('name', ''), d.get('name', ''))
    parts = [('名称', ns, 0.5)]

    qi, di = q.get('investment'), d.get('investment')
    if qi and di:
        try:
            qi, di = float(qi), float(di)
            sim = 1 - min(abs(qi - di) / max(qi, di, 1), 1)
            # 差 5% 以内算高度接近
            if sim >= 0.95:
                sim = 1.0
        except (TypeError, ValueError):
            sim = 0.0
        parts.append(('投资规模', sim, 0.3))

    qu, du = (q.get('unit') or '').strip(), (d.get('unit') or '').strip()
    if qu and du:
        same = 1.0 if qu == du else (0.7 if (qu in du or du in qu) else 0.0)
        parts.append(('建设单位', same, 0.12))

    qy, dy = q.get('year'), d.get('year')
    if qy and dy:
        try:
            gap = abs(int(qy) - int(dy))
        except (TypeError, ValueError):
            # 年度不可解析就不参与打分，别让一个脏字段把整个比对打成 500
            gap = None
        if gap is not None:
            sim = 1.0 if gap == 0 else max(0.0, 0.6 - 0.2 * (gap - 1))
            parts.append(('立项年度', sim, 0.08))

    total = sum(v * w for _, v, w in parts)
    wsum = sum(w for _, _, w in parts) or 1
    return round(total / wsum, 3), [(k, round(v, 3)) for k, v, _ in parts]


# ---------------------------------------------------------------- 疑点
#
# 这里只产出「疑点」，不产出「是否重复」的结论。
# 差别是实质性的：结论是判断，疑点是问题。
#   「高度疑似重复建设」——系统替专家下了判断，一旦误判就要背责任；
#   「同一建设单位既有系统与本项目有 5 项业务功能重合，请核实是否为改造升级」
#     ——系统只提出问题，专家带着问题去比对两份材料。
# 最终是否构成重复，只能由专家核定，并落库留痕。

FINDING_LEVELS = {
    'attention': '重点关注',
    'verify': '需要核实',
    'info': '仅供参考',
}
LEVEL_WEIGHT = {'attention': 3, 'verify': 2, 'info': 1}

_SUFFIX_UNIT = ('局', '委员会', '办公室', '中心', '厅', '委', '处')


def _norm_unit(s: str) -> str:
    s = (s or '').strip()
    s = re.sub(r'^[\u4e00-\u9fa5]{1,3}(省|市|县|区|州|盟)', '', s)
    return s


def unit_same(a: str, b: str) -> bool:
    """建设单位是不是同一家。允许简称与全称的差别。"""
    a, b = _norm_unit(a), _norm_unit(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    ga, gb = build_grams(a, keep_filler=True), build_grams(b, keep_filler=True)
    return cosine(ga, gb) >= 0.8


def build_findings(fm, dr, same_unit, is_upgrade, text_score, name_sim, sec_hits=None):
    """把比对结果整理成疑点清单。每条疑点都必须能被人工回答。

    一条疑点包含四样东西，缺一不可：
      title     一句话说哪里可疑
      detail    可疑到什么程度（数字、对象、重合项）
      ask       请专家核实什么 —— 这是疑点的落点，不是结论
      evidence  支撑判断的依据，专家照着去翻材料

    没有疑点不等于不重复，只等于「按现有材料没筛出来」，报告上会写明这一点。
    """
    findings = []
    ov = fm['overlap']
    biz = len(fm['pairs'])
    gen = fm['generic_overlap']
    same_domain = dr['same']

    def add(key, level, title, detail, ask='', evidence=None):
        findings.append({
            'key': key,
            'level': level,
            'level_label': FINDING_LEVELS[level],
            'title': title,
            'detail': detail,
            'ask': ask,
            'evidence': evidence or [],
        })

    # 1. 业务功能重合 —— 只有落在同一业务领域时才算疑点
    if same_domain and ov >= 0.6:
        add('function_overlap', 'attention', '业务功能大量重合',
            f'本次申报的功能清单与对方有 {biz} 项可对上，占本次 {fm["business_q"]} 项业务功能的 {ov:.0%}；'
            f'两份材料同属「{dr["q_domain"] or "同一"}」领域。',
            '请比对两份材料的建设内容章，逐项确认这些功能是否确为同一业务事项；'
            '若确为同一事项，还要判断是重复申报，还是本单位系统的必要延伸。',
            [{'query': p['query'], 'hit': p['hit'], 'score': p['score'],
              'query_section': p['query_section'], 'hit_section': p['hit_section']}
             for p in fm['pairs'][:8]])
    elif same_domain and ov >= 0.35:
        add('function_overlap', 'verify', '部分业务功能重合',
            f'本次申报的功能中有 {biz} 项与对方对应，占 {ov:.0%}。数量不算多，但已不宜忽略。',
            '请确认重合的这几项功能，在本次项目中属于新建、改造还是直接复用；'
            '若为复用，应说明为何不能调用既有系统、而要重新建设。',
            [{'query': p['query'], 'hit': p['hit'], 'score': p['score']}
             for p in fm['pairs'][:6]])

    # 2. 同一建设单位的既有系统 —— 换名改造最典型的特征
    if same_unit and ov >= 0.3:
        same_name = name_sim >= 0.45
        if same_name or is_upgrade:
            tail = '，'.join(x for x in (
                '材料名称高度接近' if same_name else '',
                '材料中出现改造、升级类表述' if is_upgrade else '') if x)
            add('same_unit_upgrade', 'attention', '同一单位的既有系统，可能是改造按新建申报',
                f'建设单位与对方相同，存在 {biz} 项业务功能重合（{ov:.0%}）'
                + (f'；{tail}' if tail else '') + '。',
                '请核实本项目是否就是该既有系统的改造升级。若确为改造，应按改造类项目申报、'
                '不占用新建指标；若按新建申报，需逐项说明新增功能的独立价值。',
                [{'query': p['query'], 'hit': p['hit'], 'score': p['score']}
                 for p in fm['pairs'][:8]])
        else:
            add('same_unit_overlap', 'verify', '与本单位既有系统功能重叠',
                f'建设单位相同，与既有项目有 {biz} 项业务功能重合（{ov:.0%}）。',
                '请核实两个项目是并行建设还是前后接续。一个系统拆成两次立项，'
                '既增加投资也增加运维负担，需要说明必要性。',
                [{'query': p['query'], 'hit': p['hit'], 'score': p['score']}
                 for p in fm['pairs'][:6]])

    # 3. 名称雷同 —— 单独作为疑点，因为更名重报在实践中很常见
    if name_sim >= 0.75 and not (same_unit and ov >= 0.3):
        add('name_similar', 'verify', '项目名称与历史项目高度相似',
            f'两侧名称相似度 {name_sim:.0%}。名称相近本身不构成重复，但需要排除更名重报。',
            '请确认两个项目是否为同一系统的不同阶段，或仅是名称近似而建设内容不同。')

    # 4. 材料自称改造，却抽不出功能清单 —— 仅在同一业务领域内才有意义。
    #    跨领域的两份材料都说「改造」，与是否重复毫无关系，提出来就是噪声。
    if same_domain and is_upgrade and not fm['pairs']:
        add('upgrade_no_function', 'verify', '材料自称改造升级，但提不出功能清单',
            '材料中出现改造、升级类表述，却没有可对齐的功能条目，改造范围无法判断。',
            '请要求申报单位按功能清单的格式补全「本次改造哪些功能、新增哪些功能」，'
            '否则无法核减重复投资。')

    # 5. 文本雷同但功能缺失 —— 材料写得虚
    if text_score >= 0.8 and not fm['pairs']:
        add('text_similar_no_function', 'verify', '文本高度雷同，却没有可比对的功能清单',
            f'两份材料的正文重合度 {text_score:.0%}，但都抽不出功能条目，'
            '这类情况常见于把模板改个名字就报上来。',
            '请人工翻阅两份材料的建设内容章，确认是否为同一份文本改写；'
            '必要时退回，要求补功能清单。')

    # 6. 业务领域不同 —— 写明，避免专家把通用能力重合误当线索
    if not same_domain and (gen >= 0.3 or fm['generic_matched']):
        add('cross_domain', 'info', '业务领域不同，通用能力写法接近',
            f'本次「{dr["q_domain"] or "未能识别"}」，对方「{dr["d_domain"] or "未能识别"}」，'
            f'业务对象不搭界；但通用能力有 {gen:.0%} 重合。',
            '门户、认证、共享交换这类能力本就应当复用，一般不作为重复建设的依据。'
            '两者若共用同一套底座，可请申报单位说明复用关系，不必按重复处理。',
            [{'generic': g} for g in fm['generic_matched'][:8]])

    # 7. 只列事实、不提问的参考项
    if sec_hits:
        top = sec_hits[0]
        if top['score'] >= 0.75:
            add('section_similar', 'info', f'{top["label"]}章节文本高度相似',
                f'该章文字相似度 {top["score"]:.0%}，为各章最高。文字相似不等于功能重复，'
                '可作为翻阅两份材料时的起点。')
    return findings


def screen_level(findings):
    """疑点清单的总体级别。只用于排序与标签，不是重复与否的结论。"""
    levels = {f['level'] for f in findings}
    for key in ('attention', 'verify', 'info'):
        if key in levels:
            return key
    return 'none'


def compare_one(q, d, top_snippets: int = 5):
    """q / d 均含 {grams, sections, functions, name, unit, year, investment}"""
    from . import functions as funcmod

    qg, dg = q['grams'], d['grams']
    if not qg or not dg:
        return None
    a_norm, b_norm = _l2(qg), _l2(dg)
    cos = cosine(qg, dg, a_norm, b_norm)
    cont = containment(qg, dg)
    content = 0.6 * cos + 0.4 * cont

    q_funcs = q.get('functions') or []
    d_funcs = d.get('functions') or []
    fm = funcmod.match_functions(q_funcs, d_funcs)
    dr = funcmod.domain_relation(q_funcs, d_funcs, q.get('text', ''), d.get('text', ''))

    sec_hits = compare_sections(q['sections'], d['sections'], COMPARE_CATEGORIES)
    if sec_hits:
        weighted = sum(h['score'] * h['weight'] for h in sec_hits) / sum(h['weight'] for h in sec_hits)
        sec_score = 0.4 * sec_hits[0]['score'] + 0.6 * weighted
    else:
        sec_score = 0.0

    mscore, mparts = meta_score(q, d)
    name_sim = next((v for k, v in mparts if k == '名称'), 0)
    same_unit = unit_same(q.get('unit', ''), d.get('unit', ''))
    is_upgrade = bool(q.get('is_upgrade') or d.get('is_upgrade'))

    # 功能重合是主判据，文本与章节退为佐证
    raw = 0.55 * fm['overlap'] + 0.20 * sec_score + 0.15 * content + 0.10 * mscore
    if not dr['same']:
        raw *= 0.45  # 跨领域封顶，避免「通用功能写得像」把分数顶上去
    relevance = min(max(raw, 0.0), 1.0)

    findings = build_findings(fm, dr, same_unit, is_upgrade, content, name_sim, sec_hits)
    level = screen_level(findings)

    reason, notes = [], []
    if fm['pairs']:
        top = fm['pairs'][0]
        reason.append(f"业务功能重合 {fm['overlap']:.0%}（{len(fm['pairs'])} 项），"
                      f"其中「{top['query']}」与「{top['hit']}」直接对应")
        more = [f"{p['query']}／{p['hit']}" for p in fm['pairs'][1:4]]
        if more:
            reason.append('另有：' + '、'.join(more))
    if same_unit:
        reason.append('建设单位相同')
    if is_upgrade:
        reason.append('材料中出现改造／升级类表述')
    if sec_hits and sec_hits[0]['score'] >= 0.7:
        notes.append(f"{sec_hits[0]['label']}章节文本相似度 {sec_hits[0]['score']:.0%}")
    if cos >= 0.6:
        notes.append(f'全文文本相似度 {cos:.0%}')
    if cont >= 0.7:
        notes.append(f'本篇 {cont:.0%} 的内容能在对方材料中找到对应')
    if fm['generic_matched']:
        notes.append('通用能力重合：' + '、'.join(fm['generic_matched'][:6])
                     + '（属共性能力，通常可复用，不单独作为重复依据）')

    qi, di = q.get('investment'), d.get('investment')
    if qi and di:
        try:
            qi, di = float(qi), float(di)
            if max(qi, di) > 0 and abs(qi - di) / max(qi, di) <= 0.05:
                notes.append(f'投资规模接近（{qi:g} 万元 / {di:g} 万元）')
        except (TypeError, ValueError):
            pass

    counts = {k: sum(1 for f in findings if f['level'] == k) for k in LEVEL_WEIGHT}

    return {
        'scores': {
            'relevance': round(relevance, 3),
            'content': round(content, 3),
            'cosine': round(cos, 3),
            'containment': round(cont, 3),
            'section': round(sec_score, 3),
            'meta': round(mscore, 3),
            'function': fm['overlap'],
        },
        # 只给疑点，不给结论。是否构成重复由专家核定（见 CompareReview）。
        'findings': findings,
        'finding_counts': counts,
        'screen': {
            'level': level,
            'level_label': FINDING_LEVELS.get(level, '未筛出疑点'),
            'weight': LEVEL_WEIGHT.get(level, 0),
            'total': len(findings),
            'summary': ('发现 %d 项疑点，待专家核定' % (counts['attention'] + counts['verify'])
                        if (counts['attention'] + counts['verify'])
                        else '未筛出需核实的疑点'),
        },
        'functions': {
            'overlap': fm['overlap'],
            'pairs': fm['pairs'][:12],
            'business_q': fm['business_q'],
            'business_d': fm['business_d'],
            'generic_matched': fm['generic_matched'][:8],
            'generic_overlap': fm['generic_overlap'],
            'query_list': [f['name'] for f in q_funcs if not f['generic']][:16],
            'hit_list': [f['name'] for f in d_funcs if not f['generic']][:16],
        },
        'domain': dr,
        'sections': sec_hits[:8],
        'meta_parts': mparts,
        'reasons': reason,
        'notes': notes,
    }
