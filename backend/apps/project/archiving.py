"""删除项目前的留痕归档。

起因：管理员在台账里清理项目时，已提交/已验收的项目原来一律删不掉，
只能走撤回或不予立项；真要清理测试数据，就绕不过去。放开删除之后，
必须先把「这个项目当时怎么走的」落盘 —— 流转记录、补正单、会签、
预审结果与全部原件都随项目一起 CASCADE 掉，删完就再也查不到审计线索。

规则：
- 只要删除动作生效，先归档，归档不成则中止删除（宁可不删，不留断档）。
- 归档是独立 zip，落在 data/deleted/ 下，不进媒体目录、不受清理脚本影响。
- 归档内容只读投影，不改变库里任何数据。
"""
import json
import logging
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db.models.fields.files import FieldFile

from .models import Project

log = logging.getLogger(__name__)

ARCHIVE_DIR_NAME = 'deleted'
# 结项归档：一个项目一份完整资料，落在 data/exports/，跟前端「导出归档包」对应
EXPORT_DIR_NAME = 'exports'
EXPORT_KEEP = 3

STAGE_ORDER = [c[0] for c in Project.Stage.choices]
STAGE_NAME = {**dict(Project.Stage.choices), 'draft': '申报材料'}


class ArchiveError(RuntimeError):
    """归档失败。调用方应中止删除。"""


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, FieldFile):  # 原件字段只留相对路径，不读内容
        return value.name or ''
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _fields(obj, skip=()):
    """把一条模型记录拍平成 dict，FileField 只留路径不读内容。"""
    out = {}
    for f in obj._meta.concrete_fields:
        if f.name in skip:
            continue
        out[f.name] = _jsonable(getattr(obj, f.attname, None))
    return out


def archive_dir() -> Path:
    path = Path(settings.DATA_DIR) / ARCHIVE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_payload(project, operator):
    files = list(project.files.all())
    return {
        'archived_at': _jsonable(datetime.now()),
        'archived_by': {
            'id': operator.pk,
            'username': operator.username,
            'name': getattr(operator, 'display', '') or operator.username,
            'role': operator.role,
        },
        'project': _fields(project),
        'files': [
            {**_fields(f, skip=('text',)), 'has_text': bool(f.text),
             'char_count': f.char_count or 0}
            for f in files
        ],
        'events': [_fields(e) for e in project.events.all()],
        'reports': [_fields(r) for r in project.reports.all()],
        'notices': [_fields(n) for n in project.notices.all()],
        # 评审会没有逐人账号，留痕是「三档票数 + 各专家意见表/汇总表」
        'review': {
            'result': project.review_result,
            'note': project.review_note,
            'votes': {'pass': project.review_agree,
                      'modified': project.review_modified,
                      'reject': project.review_reject},
            'files': [{'name': r.name, 'kind': r.kind,
                       'uploaded_by': r.uploaded_by.display if r.uploaded_by_id else '',
                       'created': _jsonable(r.created)}
                      for r in project.review_files.all()],
            'scan': project.review_scan_name,
            'concluded_at': _jsonable(project.concluded_at),
        },
    }


def export_project_archive(project, operator) -> dict:
    """把项目留痕与原件打包成 zip，返回 {path, name, size}。

    抛 ArchiveError 时调用方必须放弃删除。
    """
    payload = _build_payload(project, operator)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    code = (project.code or f'project{project.pk}').replace('/', '_')
    target = archive_dir() / f'{code}_{stamp}.zip'
    seq = 1
    while target.exists():
        seq += 1
        target = archive_dir() / f'{code}_{stamp}_{seq}.zip'

    try:
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('留痕.json',
                        json.dumps(payload, ensure_ascii=False, indent=2))
            zf.writestr('README.txt', _readme(project, operator))
            for f in project.files.all():
                src = Path(f.file.path) if f.file else None
                if not src or not src.exists():
                    continue
                # 文件名可能重复，前缀 id 保证 zip 内唯一且仍可辨认；
                # 文件名来自用户上传，先过 _safe() 去掉 / \ : 等路径字符，
                # 否则条目名可含 ../ 造成 Zip Slip（解压到目录外）
                zf.write(src, f'原件/{f.id}_{_safe(f.filename)}')
            # 签字件是评审结论的原始凭证，项目删了也必须跟着走，
            # 否则事后只能看到三个票数，说不清是谁签的
            for r in project.review_files.all():
                src = Path(r.file.path) if r.file else None
                if not src or not src.exists():
                    continue
                zf.write(src, f'评审会材料/{r.id}_{_safe(r.name)}')
    except Exception as exc:  # 磁盘满、权限不足、原件读不到
        log.exception('项目归档失败 project=%s', project.pk)
        target.unlink(missing_ok=True)
        raise ArchiveError(str(exc)) from exc

    return {'path': str(target), 'name': target.name, 'size': target.stat().st_size}


def _readme(project, operator):
    return (
        f'项目删除留痕归档\n'
        f'项目编号：{project.code}\n'
        f'项目名称：{project.name}\n'
        f'申报单位：{project.unit}\n'
        f'删除时阶段：{project.get_stage_display()}\n'
        f'删除时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n'
        f'删除人：{getattr(operator, "display", "") or operator.username}\n\n'
        f'留痕.json 内为项目基本信息、流转记录、预审结果、补正单、评审会结论与材料清单；\n'
        f'原件/ 下为删除前的申报材料原件副本；\n'
        f'评审会材料/ 下为各专家意见表与汇总意见表的扫描件。\n'
        f'本归档只用于事后追溯，不参与流程流转；如需恢复项目，须人工按留痕.json 重建。\n'
    )


# ======================================================================
# 结项归档：一个项目一份完整资料
# 与删除留痕分开：那个是「怕删了查不到」，这个是「办完了要交得出去」。
# 结项后单位要存档、要报审计、要移交档案室，靠翻系统不现实 —— 得能一次拿走。
# ======================================================================
def export_dir() -> Path:
    path = Path(settings.DATA_DIR) / EXPORT_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe(name, fallback='未命名'):
    s = (name or '').replace('/', '_').replace('\\', '_').replace(':', '_').strip()
    return s or fallback


def _fields_public(obj, operator=None):
    """导出给申报单位时的字段裁剪（审查项 #7）。

    详情接口对申报单位删掉了 PrecheckReport.items / findings、裁掉了
    dup_check 里的他家项目名与原文片段。归档包此前用 _fields() 全量导出，
    等于把详情刻意藏起来的内部底稿又从 zip 里发出去 —— 这里对齐详情口径。
    """
    data = _fields(obj)
    if getattr(operator, 'role_code', '') != 'applicant':
        return data
    if hasattr(obj, 'items') and hasattr(obj, 'findings'):
        data.pop('items', None)
        data.pop('findings', None)
    if hasattr(obj, 'dup_check'):
        d = obj.dup_check or {}
        keep = ('level', 'level_label', 'summary', 'scanned', 'compared',
                'checked_at', 'version', 'note')
        data['dup_check'] = {k: d[k] for k in keep if k in d}
    return data


def _conclusions(project, operator=None):
    """过程结论单独成文：审计要看的不是正文，是「谁在哪一步下了什么结论」。"""
    return {
        '评审会': {
            '结论': project.get_review_result_display() if project.review_result else '',
            '意见': project.review_note,
            '票数': {'通过': project.review_agree,
                     '修改后通过': project.review_modified,
                     '不通过': project.review_reject},
            '结论时间': _jsonable(project.concluded_at),
        },
        '费用评审': {
            '结论': project.get_audit_result_display() if project.audit_result else '',
            '意见': project.audit_note,
            '时间': _jsonable(project.audited_at),
        },
        '财政审核': {
            '结论': project.get_fiscal_result_display() if project.fiscal_result else '',
            '意见': project.fiscal_note,
            '时间': _jsonable(project.fiscal_at),
        },
        '立项': {
            '批复文号': project.approval_no,
            '说明': project.approval_note,
            '时间': _jsonable(project.approval_at),
        },
        '试运行与数据归集': {
            '试运行情况': project.trial_summary,
            '数据归集情况': project.data_collect_scope,
            '申请验收时间': _jsonable(project.acceptance_applied_at),
        },
        '预审结果': [_fields_public(r, operator) for r in project.reports.all()],
        '补正单': [_fields(n) for n in project.notices.all()],
    }


def export_project_package(project, operator=None) -> dict:
    """把项目打成一个 zip：材料（按环节、含历史版本）+ 生成文书 + 评审会材料 + 过程留痕。

    落 data/exports/，同项目只留最近 EXPORT_KEEP 份，避免反复导出把磁盘撑满。

    阶段硬门槛（用户口径）：只有已验收项目能导出。办到一半的件是半套材料，
    既不能当档案也不能当凭证，任何调用方都不得以任何角色绕过。
    """
    if project.stage != Project.Stage.ACCEPTED:
        raise ArchiveError(
            f'项目尚未验收（当前阶段：{project.get_stage_display()}），'
            '只有已验收项目才能导出归档包；在办件是半套材料，一律不予导出。')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    code = _safe(project.code or f'project{project.pk}')
    target = export_dir() / f'{code}_项目归档_{stamp}.zip'
    seq = 1
    while target.exists():
        seq += 1
        target = export_dir() / f'{code}_项目归档_{stamp}_{seq}.zip'

    try:
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('README.txt', _package_readme(project, operator))
            zf.writestr('04_过程/项目信息.json',
                        json.dumps(_fields_public(project, operator),
                                   ensure_ascii=False, indent=2))
            zf.writestr('04_过程/流转记录.json',
                        json.dumps([_fields(e) for e in project.events.all()],
                                   ensure_ascii=False, indent=2))
            zf.writestr('04_过程/结论与意见.json',
                        json.dumps(_conclusions(project, operator),
                                   ensure_ascii=False, indent=2))

            # 申报材料按环节分目录，文件名前缀版本信息：补正过几轮，一眼能看出哪份是最终的
            for f in project.files.all().order_by('stage', 'round', 'version', 'id'):
                src = Path(f.file.path) if f.file else None
                if not src or not src.exists():
                    continue
                folder = STAGE_NAME.get(f.stage, '未标注环节')
                tag = '当前版' if f.is_current else '历史版'
                vname = f'第{f.round}轮-第{f.version}版_{tag}_{_safe(f.filename)}'
                # 前缀 id 保证同名不覆盖（补正时同名文件很常见）
                zf.write(src, f'01_申报材料/{folder}/{f.id}_{vname}')

            for d in project.documents.all().order_by('created'):
                src = Path(d.file.path) if d.file else None
                if not src or not src.exists():
                    continue
                zf.write(src, f'02_生成文书/{d.id}_{_safe(d.name)}')

            for r in project.review_files.all().order_by('created'):
                src = Path(r.file.path) if r.file else None
                if not src or not src.exists():
                    continue
                zf.write(src, f'03_评审会材料/{r.id}_{_safe(r.name)}')

            if project.review_scan:
                src = Path(project.review_scan.path)
                if src.exists():
                    zf.write(src, f'03_评审会材料/评审意见扫描件_{_safe(project.review_scan_name or src.name)}')
    except Exception as exc:  # 磁盘满、权限不足、原件读不到
        log.exception('项目归档包生成失败 project=%s', project.pk)
        target.unlink(missing_ok=True)
        raise ArchiveError(str(exc)) from exc

    _prune_exports(code)
    return {'path': str(target), 'name': target.name, 'size': target.stat().st_size}


def _prune_exports(code):
    """同一项目只留最近几份：导出的包是给人下载的，不是版本库。"""
    olds = sorted(export_dir().glob(f'{code}_项目归档_*.zip'),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for p in olds[EXPORT_KEEP:]:
        p.unlink(missing_ok=True)


def _package_readme(project, operator):
    who = ''
    if operator is not None:
        who = (getattr(operator, 'display', '') or operator.username)
    return (
        f'项目归档资料\n'
        f'====================\n'
        f'项目编号：{project.code}\n'
        f'项目名称：{project.name}\n'
        f'申报单位：{project.unit}\n'
        f'联系人：{project.contact_name or "—"}　电话：{project.contact_phone or "—"}\n'
        f'当前阶段：{project.get_stage_display()}\n'
        f'导出时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n'
        f'导出人：{who or "—"}\n\n'
        f'目录说明\n'
        f'  01_申报材料/　按申报环节分文件夹，含各轮历史版本（文件名标注轮次与版本）\n'
        f'  02_生成文书/　系统按模板生成的预审修正说明、费用评审任务书、立项批复等\n'
        f'  03_评审会材料/　专家意见表、汇总意见表、扫描件\n'
        f'  04_过程/　项目信息、流转记录、各环节结论与意见（JSON，可用记事本或 Excel 打开）\n\n'
        f'说明：本包为该项目的完整留档，不含合同、中标通知书等由建设单位自行保管的过程档案。\n'
    )
