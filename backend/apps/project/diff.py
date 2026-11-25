"""补正前后材料对比。

补正要回答的问题是「改了什么」，不是「又传了一份」。原来只让申报人手写
「补正了几条」，初审仍得把两份文件从头翻一遍 —— 所以这里做段落级对照，
直接指出新增、删除、改写的段落。

不做字符级 diff：可研是排版文档，一个字一个字比会出来满屏红绿，
真正要看的是「多了一节网络安全」「投资概算那一段改了口径」。
"""
import difflib
import re

# 单块超过这个长度就截断：前端是左右两栏对照，太长反而看不出改在哪
MAX_BLOCK = 1600
MAX_BLOCKS = 120


def split_paragraphs(text):
    """按行切块；一行过长再按句末切。

    docx/PDF 解析出来的正文基本是「一段一行」，空行并不可靠 —— 原来只按空行
    切，整份可研会变成 1 段，对比结果就是「改写 1 段」，等于没比。
    """
    s = (text or '').replace('\r\n', '\n')
    out = []
    for raw in s.split('\n'):
        line = raw.strip()
        if not line:
            continue
        if len(line) <= 600:
            out.append(line)
            continue
        buf = ''
        for sent in re.split(r'(?<=[。；！？])', line):
            if not sent:
                continue
            if buf and len(buf) + len(sent) > 400:
                out.append(buf)
                buf = sent
            else:
                buf += sent
        if buf:
            out.append(buf)
    return out


def compare_texts(old_text, new_text):
    """返回段落级差异。same 块不回传，只报数量。"""
    pa = split_paragraphs(old_text)
    pb = split_paragraphs(new_text)
    sm = difflib.SequenceMatcher(None, pa, pb, autojunk=False)
    stats = {'same': 0, 'added': 0, 'removed': 0, 'changed': 0}
    blocks = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            stats['same'] += (i2 - i1)
            continue
        before = '\n'.join(pa[i1:i2])
        after = '\n'.join(pb[j1:j2])
        if tag == 'insert':
            stats['added'] += 1
            blocks.append({'type': 'add', 'before': '', 'after': after[:MAX_BLOCK]})
        elif tag == 'delete':
            stats['removed'] += 1
            blocks.append({'type': 'del', 'before': before[:MAX_BLOCK], 'after': ''})
        else:
            stats['changed'] += 1
            blocks.append({'type': 'change',
                           'before': before[:MAX_BLOCK],
                           'after': after[:MAX_BLOCK]})
    return {
        'stats': stats,
        'chars': {'before': len(old_text or ''), 'after': len(new_text or '')},
        'blocks': blocks[:MAX_BLOCKS],
        'truncated': len(blocks) > MAX_BLOCKS,
        'total_blocks': len(blocks),
    }


def summarize(result):
    """一句话说清改了什么，用于流转记录与补正单回执。"""
    st = result['stats']
    parts = []
    if st['added']:
        parts.append(f'新增 {st["added"]} 段')
    if st['removed']:
        parts.append(f'删除 {st["removed"]} 段')
    if st['changed']:
        parts.append(f'改写 {st["changed"]} 段')
    if not parts:
        return '正文没有段落级变化（可能只改了排版）'
    c = result['chars']
    delta = c['after'] - c['before']
    sign = '+' if delta >= 0 else '−'
    return (f'{"、".join(parts)}；字数 {c["before"]:,} → {c["after"]:,}'
            f'（{sign}{abs(delta):,}）')


def select_pair(files, current):
    """找出 current 的同类型上一版。"""
    prev = (files.filter(doc_type=current.doc_type, version__lt=current.version)
            .order_by('-version').first())
    return prev


def summarize_round(project):
    """本轮每份材料相对上一版改了什么，逐条返回。

    补正回复不再由申报人手填「补了 3 条」—— 手填的和实际改的经常对不上，
    初审还得自己核。这里直接算出来，人只需要补一句「为什么这么改」。
    """
    out = []
    files = project.files.all()
    for cur in files.filter(is_current=True).exclude(status='failed'):
        prev = select_pair(files, cur)
        if not prev or not prev.text or not cur.text:
            continue
        try:
            r = compare_texts(prev.text, cur.text)
        except Exception:      # 比对失败不能挡住补正提交
            continue
        out.append({'file': cur.filename, 'doc_type': cur.doc_type,
                    'from_version': prev.version, 'to_version': cur.version,
                    'summary': summarize(r), 'stats': r['stats'],
                    'chars': r['chars']})
    return out
