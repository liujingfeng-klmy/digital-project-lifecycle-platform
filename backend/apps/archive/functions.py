"""功能级比对。

为什么必须做到功能这一层：
  文本相似说明不了功能重复。两个毫不相干的部门都会写「统一门户」「数据共享」
  「身份认证」，文字必然像，但这不叫重复建设 —— 这些是共性能力，本来就该复用。
  反过来，一个部门把老系统换个名字重新申报改造，文字改写了七八成，
  文本相似度可能只有 0.4，但功能清单几乎一模一样，这才是真正的重复建设。

所以判重的基本单位从「段落」换成「功能项」，并且要把两类功能分开：

  通用能力   统一门户、单点登录、日志审计、数据共享交换……
             名字相同是正常的，重合不算重复，只作参考
  业务功能   不动产登记办理、医保基金监管、学籍管理……
             这类重合才是重复建设的实质证据

再叠加一个维度：业务对象。看两份材料讲的是不是同一批业务实体
（不动产/房屋/土地 vs 医保/病种/药品）。业务对象不搭界，通用功能再像也不判重
—— 这正是「名义上功能差不多、实际不一样」的情形。
"""
import re
from collections import Counter, defaultdict

# ---------------------------------------------------------------- 词表

# 通用能力：几乎任何政务系统都会有，名字重合不构成重复建设
GENERIC_WORDS = (
    '统一门户', '门户网站', '门户', '单点登录', '统一登录', '统一认证',
    '身份认证', '实名认证', '用户管理', '权限管理', '角色管理', '组织架构',
    '日志审计', '操作日志', '审计日志', '数据共享', '共享交换', '数据交换',
    '数据接口', '接口服务', '数据目录', '资源目录', '标准规范', '数据治理',
    '统计分析', '统计报表', '可视化', '大屏', '驾驶舱', '移动端', '移动应用',
    '短信', '消息推送', '消息通知', '工作流', '流程引擎', '电子签章', '电子印章',
    '电子证照', '电子档案', '在线支付', '在线缴费', '扫码', '人脸识别',
    '报表', '通知公告', '系统管理', '后台管理', '运维监控', '监控告警',
    '安全防护', '等级保护', '备份', '容灾', '数据备份', '日志管理',
    '统一身份', '密码应用', '国密', '运维管理', '配置管理', '字典管理',
)

# 业务对象：判「是不是同一个业务领域」靠它，不靠文字像不像
DOMAIN_OBJECTS = {
    '不动产': ('不动产', '房屋', '房产', '土地', '宗地', '林权', '海域', '产权',
               '登记簿', '不动产权证', '分户图', '楼盘表'),
    '医保': ('医保', '医疗保障', '病种', '药品', '耗材', '医用耗材', '结算',
             '基金', '参保', '定点', '住院', '门诊', '报销', '诊疗', '处方'),
    '教育': ('学籍', '课程', '教师', '学生', '教学', '教研', '招生', '班级',
             '作业', '课堂', '教材', '校园', '学校'),
    '市场监管': ('企业', '法人', '信用', '许可证', '营业执照', '监管', '执法',
                 '抽查', '商事', '登记注册'),
    '人社': ('社保', '养老', '就业', '失业', '工伤', '人才', '人事', '劳动'),
    '税务': ('税务', '发票', '纳税', '缴税', '税收', '退税'),
    '交通': ('交通', '车辆', '运输', '路网', '客运', '货运', '驾驶员', '道路'),
    # 「环境」「监测」在可研里多半指运行环境、性能监测，不是生态环境，不能收
    '生态环境': ('环保', '污染', '排污', '废气', '废水', '排放'),
    '财政': ('国库', '政府采购', '非税', '资金拨付', '预算指标'),
    '公安': ('户籍', '人口', '治安', '案件', '警情', '出入境', '居住证'),
    '住建': ('施工', '招投标', '建筑', '住房', '物业', '公积金'),
}

_OBJECT2DOMAIN = {}
for _dom, _words in DOMAIN_OBJECTS.items():
    for _w in _words:
        _OBJECT2DOMAIN[_w] = _dom

UPGRADE_WORDS = ('升级改造', '改造升级', '升级', '改造', '扩建', '扩容', '二期',
                 '续建', '优化提升', '迭代升级', '功能扩展', '版本升级')

# ---------------------------------------------------------------- 抽取

# 「建设不动产登记业务办理子系统」
_RE_SYSTEM = re.compile(
    r'(?:建设|新建|开发|构建|打造|升级|改造|完善|搭建)'
    r'([^，。；：、\n|]{2,24}?)(子系统|模块|平台|系统)')

# 「包含受理、审核、登簿、发证四个核心环节」「提供业务预约、进度查询等功能」
# 引号要排除：政务文本里「支撑“一窗受理、并行办理”业务」这种写法，
# 不排除引号会把功能名从引号中间截断，抽出「一窗受理」这类半截货
_RE_ENUM = re.compile(
    r'(?:包含|包括|覆盖|支持|提供|实现|具备|满足|用于|保留|优化|改造|升级'
    r'|整合|涵盖|涉及|围绕|新建|建设|承载|承担|开展)'
    r'([^。；\n|，,“”"\']{2,90}?)'
    r'(等功能模块|等业务模块|等应用模块|等功能|等能力|等模块|业务模块|应用模块'
    r'|子模块|子系统|功能模块|功能|能力|模块|环节|场景)')

# 「（一）登记业务办理」「1. 进度查询」这类分条小标题，是功能名最规整的来源
_RE_SUB = re.compile(
    r'^(?:[（(][一二三四五六七八九十\d]+[）)]|\d+[.、．]|[①-⑳])\s*([^，。；\n|]{2,18})')

# 表格行：功能清单表（第一列功能名，第二列说明）
_RE_TABLE = re.compile(r'^([^|\n]{2,22})\s*\|\s*([^|\n]{1,60})')

_MODIFIERS = (
    '全市统一的', '全省统一的', '全市统一', '全省统一', '全国统一', '统一的',
    '覆盖全市的', '面向全市的', '全市', '全省', '全区', '全县', '本级',
    '新一代', '新型', '一套', '一个', '各类', '相关', '综合性的',
)
_TAIL = ('子系统', '子平台', '功能模块', '模块', '平台', '系统', '功能')
_BAD_TABLE = ('费', '合计', '小计', '金额', '科目', '序号', '名称', '说明',
              '项目', '年度', '单位', '比例', '备注', '内容')

# 这些词打头的基本是句子残片而不是功能名：「实现登记」「改造完成后」「建设一个独立系统」
_BAD_START = ('实现', '建设', '新建', '改造', '升级', '优化', '整合', '完成',
              '采用', '按照', '根据', '通过', '其中', '包括', '包含', '支持',
              '提供', '用于', '确保', '围绕', '涉及', '涵盖', '承载', '承担',
              '继续', '支撑', '覆盖', '面向', '保留', '开展', '推进', '建立',
              '一个', '一套', '在', '对', '将', '把', '为', '从', '由此')
# 含这些词说明抓到了整句的碎片，不是功能名
_BAD_STREAM = ('完成', '采用', '按照', '通过', '其中', '如下', '期间', '新旧',
               '既有', '原有', '方面', '基础上', '分别', '同时', '进一步',
               '以及', '一个', '一套', '进行', '不同', '之间', '之后', '之前',
               '领域', '主要', '相关', '总体')


def _clean_name(s: str) -> str:
    s = (s or '').strip(' 　、，,：:（）()【】[]“”"\'')
    s = re.sub(r'^[（(]?[一二三四五六七八九十\d]+[）)]?[、.．]?\s*', '', s)
    # 「覆盖全市中小学的智慧教育」→「智慧教育」
    s = re.sub(r'^(覆盖|面向|针对|围绕|服务)[^的]{0,16}的', '', s)
    # 「按学科」这类列举残片
    s = re.sub(r'^按(?=.)', '', s)
    for m in _MODIFIERS:
        if s.startswith(m):
            s = s[len(m):]
    # 长名字后面的定语尾巴：「发证的全流程线上办理」→「发证」
    if len(s) >= 9:
        s = re.sub(r'的[^的]{0,10}$', '', s)
    for t in _TAIL:
        if s.endswith(t) and len(s) > len(t) + 1:
            s = s[: -len(t)]
    s = re.sub(r'(业务|环节|功能|场景|方面)$', '', s)
    return s.strip()


def _strip_count(s: str) -> str:
    """去掉「四个核心」「等三类」「共 12 项」这类计量尾巴。"""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r'[，,]?\s*(?:等|共)?[一二三四五六七八九十两\d]+\s*(?:个|类|项|大)?\s*'
                   r'(?:核心|主要|重点|基本|业务|子)?\s*$', '', s)
    return s.strip()


def _is_generic(name: str) -> bool:
    for w in GENERIC_WORDS:
        if w in name:
            return True
    return False


def objects_of(text: str) -> set:
    found = set()
    for w in _OBJECT2DOMAIN:
        if w in text:
            found.add(w)
    return found


def domain_profile(objects) -> Counter:
    c = Counter()
    for o in objects:
        d = _OBJECT2DOMAIN.get(o)
        if d:
            c[d] += 1
    return c


def _strip_etc(s: str) -> str:
    """去掉「查封登记等」「自助打证机等多渠道」这类尾巴。"""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r'等[^、，,]{0,6}$', '', s)
        s = re.sub(r'(等等|等)$', '', s)
    return s.strip()


# 两字功能名只有这些是真功能，其余（「公众」「自助」）是句子碎片
KEY_SHORT = {'受理', '审核', '登簿', '发证', '查询', '缴费', '预约', '登记',
             '结算', '监管', '审批', '归档', '核验', '预警', '统计', '接件',
             '补正', '公告', '制证', '收费', '发照', '备案', '申报', '复核'}


def _add(bucket, name, section, evidence, source, ctx_objects=()):
    name = _strip_etc(_clean_name(name))
    if not (2 <= len(name) <= 18):
        return
    if re.fullmatch(r'[\d.\s]+', name):
        return
    if len(name) <= 2 and name not in KEY_SHORT and name not in _OBJECT2DOMAIN:
        return
    if any(name.startswith(w) for w in _BAD_START):
        return
    if any(w in name for w in _BAD_STREAM):
        return
    key = name
    if key in bucket:
        bucket[key]['hits'] += 1
        return
    # 业务归属不只看这句，还要看所在章节 —— 「支持 12 类登记业务的全流程办理」
    # 这句话里没出现「不动产」，但它属于不动产那一章，功能归属由章节决定
    objs = set(objects_of(name)) | set(objects_of(evidence)) | set(ctx_objects)
    bucket[key] = {
        'name': name,
        'generic': _is_generic(name),
        'obj_name': sorted(objects_of(name)),
        'objects': sorted(objs),
        'section': section,
        'evidence': evidence[:120],
        'source': source,
        'hits': 1,
    }


def extract_functions(sections) -> list:
    """从章节里抽功能项。

    只扫「建设内容」和「需求分析」两章 —— 功能清单本来就出自这里。
    全文扫会把概述里的「改造完成后系统更名为…」、技术路线里的
    「采用前后端分离架构」全当成功能，分母虚高，重合度被稀释。
    """
    zones = [s for s in (sections or [])
             if s.get('category') in ('plan', 'demand')
             and len(s.get('text') or '') >= 80]
    if not zones:
        zones = sections or []

    bucket = {}
    for sec in zones:
        title = sec.get('title', '')
        cat = sec.get('category', 'other')
        text = sec.get('text', '') or ''
        ctx_objects = objects_of(title + text)

        # 分条小标题：「（一）登记业务办理」
        for line in text.splitlines():
            s = line.strip()
            m = _RE_SUB.match(s)
            if m:
                _add(bucket, m.group(1), title, s[:120], 'sub', ctx_objects)

        # 表格行
        for line in text.splitlines():
            m = _RE_TABLE.match(line.strip())
            if not m:
                continue
            first = m.group(1).strip()
            if any(b in first for b in _BAD_TABLE):
                continue
            _add(bucket, first, title, line.strip(), 'table', ctx_objects)

        # 句子级抽取。先把换行并掉：Word 文档里一句常被拆成两行，
        # 「建设学籍业务办理、招生报名…五个业务模块」断在「五个」前后就抽不出来
        flat = text.replace('\n', '')
        for sent in re.split(r'(?<=[。；;])', flat):
            sent = sent.strip()
            if len(sent) < 6:
                continue
            for m in _RE_SYSTEM.finditer(sent):
                _add(bucket, m.group(1) + m.group(2), title, sent, 'system', ctx_objects)
            for m in _RE_ENUM.finditer(sent):
                body = _strip_count(m.group(1))
                # 先按逗号切开：「优化操作界面与流转规则，继续支撑…」这种长串，
                # 后半截整句都不是功能名，靠 _BAD_START 兜住
                chunks = [c for c in re.split(r'[，,]', body) if c.strip()]
                parts = []
                for c in chunks:
                    parts.extend([p for p in re.split(r'[、/]|与', c) if p.strip()])
                if len(parts) >= 2:
                    for p in parts:
                        _add(bucket, p, title, sent, 'enum', ctx_objects)
                elif len(parts) == 1:
                    _add(bucket, parts[0], title, sent, 'enum', ctx_objects)

    funcs = list(bucket.values())
    # 只被抽到一次、且名称很短又没业务对象的，基本是噪声
    out = []
    for f in funcs:
        if f['hits'] <= 1 and len(f['name']) <= 2 and not f['objects']:
            continue
        out.append(f)

    # 补全篇主领域的对象。单个句子未必出现领域词 —— 「支持 12 类登记业务的全流程办理」
    # 通篇没写「不动产」三字，但它属于不动产那一章。整篇讲什么，由全文对象分布定。
    # 领域以功能项名称为准。全文统计容易被「政务云环境」「测试环境」这类词带偏，
    # 把一份不动产的改造方案判成生态环境项目。名称里看不出领域时，才退回全文。
    name_obj = set()
    for f in out:
        name_obj |= objects_of(f['name'])
    all_obj = set()
    for sec in sections or []:
        all_obj |= objects_of(sec.get('title', '') + (sec.get('text', '') or ''))
    profile = domain_profile(name_obj) or domain_profile(all_obj)
    if profile:
        main_dom = profile.most_common(1)[0][0]
        dom_objs = set(DOMAIN_OBJECTS.get(main_dom, ()))
        for f in out:
            f['objects'] = sorted(set(f['objects']) | dom_objs)
            f['domain'] = main_dom
    return out


# ---------------------------------------------------------------- 匹配

def _bigrams(s: str) -> set:
    s = re.sub(r'[^\u4e00-\u9fa5A-Za-z0-9]', '', s)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


ACTIONS = ('办理', '登记', '审核', '审批', '查询', '缴费', '支付', '监管', '监管',
           '申报', '备案', '签发', '核验', '归档', '统计', '分析', '预警',
           '调度', '采集', '共享', '交换', '认证', '预约', '结算', '清算',
           '分配', '测算', '评估', '考核', '巡查', '执法', '开具', '打印')


def _actions(s: str) -> set:
    return {a for a in ACTIONS if a in s}


def func_sim(a: dict, b: dict) -> float:
    """两个功能项的相似度。字面 + 动作 + 业务对象，三者一起看。

    只靠字面：「不动产登记业务办理」和「不动产综合业务办理平台」字面 0.5，
    会漏。加上动作（都有「办理」）和对象（都有「不动产」「登记」）才稳。

    反过来，业务对象是防误报的关键：「审核」和「医保智能审核」字面完全包含，
    但一个挂在不动产、一个挂在医保上，不是同一个功能。
    """
    ga, gb = _bigrams(a['name']), _bigrams(b['name'])
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    lit = inter / min(len(ga), len(gb))
    act = 1.0 if (_actions(a['name']) & _actions(b['name'])) else 0.0
    # 对象优先看功能名本身：「学籍业务办理」所在章节可能提到房产（数据交换），
    # 但那不代表它是不动产的功能。名称里看不出对象时，才用所在章节的对象兜底。
    oa = set(a.get('obj_name') or a['objects'])
    ob = set(b.get('obj_name') or b['objects'])
    obj = 1.0 if (oa & ob) else (0.0 if (oa or ob) else 0.5)
    score = 0.40 * lit + 0.25 * act + 0.35 * obj
    # 「审核」「查询」这种两字泛功能，靠字面就能全中，必须压一压，
    # 否则任何两个系统都会因为「审核」二字被判成功能重复
    if min(len(a['name']), len(b['name'])) <= 3:
        score *= 0.85
    return round(score, 3)


def match_functions(q_funcs, d_funcs, threshold: float = 0.62):
    """一对一匹配，返回匹配对与重合度。

    用全局贪心：先把所有候选对按分数降序排，再依次认领。
    按某一边的顺序逐个找最优会让高分对撞掉 —— 「不动产登记信息」先抢走了
    「不动产登记业务办理」（0.87），真正完全相同的 1.00 那对反而配不上。
    """
    q_biz = [f for f in q_funcs if not f['generic']]
    d_biz = [f for f in d_funcs if not f['generic']]
    q_gen = [f for f in q_funcs if f['generic']]
    d_gen = [f for f in d_funcs if f['generic']]

    cand = []
    for qi, qf in enumerate(q_biz):
        for di, df in enumerate(d_biz):
            s = func_sim(qf, df)
            if s >= threshold:
                cand.append((s, qi, di))
    cand.sort(reverse=True)

    used_q, used_d = set(), set()
    pairs = []
    for s, qi, di in cand:
        if qi in used_q or di in used_d:
            continue
        used_q.add(qi)
        used_d.add(di)
        qf, df = q_biz[qi], d_biz[di]
        pairs.append({
            'query': qf['name'],
            'hit': df['name'],
            'score': s,
            'query_section': qf['section'],
            'hit_section': df['section'],
            'evidence': qf['evidence'],
        })

    gen_pairs = []
    for qf in q_gen:
        for df in d_gen:
            if qf['name'] == df['name'] or func_sim(qf, df) >= 0.75:
                gen_pairs.append(qf['name'])
                break

    denom = max(1, min(len(q_biz), len(d_biz)))
    overlap = len(pairs) / denom
    gen_overlap = len(gen_pairs) / max(1, min(len(q_gen), len(d_gen)))

    return {
        'pairs': sorted(pairs, key=lambda p: -p['score']),
        'overlap': round(min(overlap, 1.0), 3),
        'business_q': len(q_biz),
        'business_d': len(d_biz),
        'generic_q': len(q_gen),
        'generic_d': len(d_gen),
        'generic_matched': sorted(set(gen_pairs)),
        'generic_overlap': round(min(gen_overlap, 1.0), 3),
    }


def domain_relation(q_funcs, d_funcs, q_text='', d_text=''):
    """判断两份材料是不是同一个业务领域。

    只看对象交集会被通用词污染：每份可研的投资章都有「工程」「财政」「预算」，
    交集永远不空，于是所有项目都被判成同一领域。所以改看主领域是否一致。
    """
    q_obj = set()
    d_obj = set()
    for f in q_funcs:
        q_obj |= set(f['objects'])
    for f in d_funcs:
        d_obj |= set(f['objects'])
    q_obj |= objects_of(q_text[:4000])
    d_obj |= objects_of(d_text[:4000])

    shared = q_obj & d_obj
    qp = Counter(f.get('domain') for f in q_funcs if f.get('domain'))
    dp = Counter(f.get('domain') for f in d_funcs if f.get('domain'))
    q_main = qp.most_common(1)[0][0] if qp else ''
    d_main = dp.most_common(1)[0][0] if dp else ''
    if not q_main or not d_main:
        q_pf, d_pf = domain_profile(q_obj), domain_profile(d_obj)
        q_main = q_main or (q_pf.most_common(1)[0][0] if q_pf else '')
        d_main = d_main or (d_pf.most_common(1)[0][0] if d_pf else '')

    if q_main and d_main:
        same = q_main == d_main
    elif len(shared) >= 2:
        same = True
    else:
        same = False

    return {
        'same': same,
        'q_domain': q_main,
        'd_domain': d_main,
        'q_objects': sorted(q_obj)[:12],
        'd_objects': sorted(d_obj)[:12],
        'shared_objects': sorted(shared)[:12],
    }


UPGRADE_STRONG = ('升级改造', '改造升级', '改扩建', '迭代升级', '版本升级',
                  '功能扩展', '二期建设', '续建')
UPGRADE_WEAK = ('升级', '改造', '扩建', '扩容', '二期', '续建', '优化提升')


def upgrade_hint(text: str, sections) -> dict:
    """材料是否在说「这是对既有系统的改造」。

    「升级」「改造」这两个词在可研里出现得太泛（「改造营商环境」也算），
    单看有没有出现必然误判，所以分强弱：
      强词（升级改造、二期建设、功能扩展）出现一次即可；
      弱词（升级、改造）要出现两次以上才算，且只能作为旁证。
    """
    head = (text or '')[:6000]
    strong = [w for w in UPGRADE_STRONG if w in text]
    weak_hits = sum(text.count(w) for w in UPGRADE_WEAK)
    weak_words = [w for w in UPGRADE_WEAK if w in text]
    is_upgrade = bool(strong) or weak_hits >= 2
    # 标题里直接写「改造」「升级」，是最明确的信号
    for sec in sections or []:
        t = sec.get('title', '') or ''
        if any(w in t for w in ('改造', '升级', '扩建', '续建')):
            strong.append(t[:20])
            is_upgrade = True
            break
    return {
        'is_upgrade': is_upgrade,
        'strong': bool(strong),
        'words': sorted(set(strong or weak_words))[:6],
    }
