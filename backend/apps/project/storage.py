"""文件落盘路径：一个项目一个文件夹。

原来按「年月」分：media/project/202611/、media/generated/202611/、
media/review/202611/ —— 一个月里十几个项目的材料混在一起，同名文件靠
随机后缀区分。要在系统外找某个项目的资料，只能一个个点开看属主；
要拷给审计，得在目录里挑。而项目本身就是天然的归档单元，没理由不按它分。

现在的结构：

    media/projects/XM2026-012_市不动产登记信息平台升级改造/
        01_申报材料/01_申报中/…
        01_申报材料/02_预审中/…
        02_生成文书/立项批复-XM2026-012.docx
        03_评审会材料/专家意见表-1.pdf

编号在前保证排序稳定、名称在后保证一眼认得出。文件夹名在第一次落盘时
定型，之后改项目名不搬动 —— 数据库里存的是相对路径，搬一次就要改一批
记录，收益只有「文件夹好看」，不值。
"""
import re

# 项目名放进路径前先截断：Windows 全路径有 260 字符上限，
# 中文项目名动辄三四十字，不截断会出现「文件明明传了却打不开」
NAME_LIMIT = 40

# 路径里不能出现的字符（macOS/Linux 上冒号可留，但拷到 Windows 会炸）
_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]')


def clean(text, fallback='未命名', limit=None):
    """把任意名称压成一段可以安全当文件名/目录名的文本。"""
    s = _BAD.sub('_', (text or '').strip())
    s = re.sub(r'\s+', ' ', s).strip(' .')
    if limit:
        s = s[:limit].strip(' .')
    return s or fallback


def project_folder(project):
    """media/projects/编号_项目名。"""
    code = clean(getattr(project, 'code', ''), f'P{project.pk or 0}', 24)
    name = clean(getattr(project, 'name', ''), '', NAME_LIMIT)
    return f'projects/{code}_{name}' if name else f'projects/{code}'


# 环节目录带序号，按流程顺序排，不用记哪个先哪个后。
# 序号取 Project.Stage 的声明顺序 —— 那个顺序就是流程顺序，
# 以后加环节不用回来改这张表
def stage_folder(stage):
    from .models import Project  # 延迟导入：models 会反向引用本模块

    if not stage:
        return '00_未标环节'
    for i, item in enumerate(Project.Stage, start=1):
        if item.value == stage:
            return f'{i:02d}_{item.label}'
    return f'99_{clean(stage)}'


def upload_material(instance, filename):
    """申报材料：01_申报材料/环节/文件名。"""
    return (f'{project_folder(instance.project)}/01_申报材料/'
            f'{stage_folder(instance.stage)}/{clean(filename)}')


def upload_generated(instance, filename):
    """系统生成文书：02_生成文书/文件名。"""
    return f'{project_folder(instance.project)}/02_生成文书/{clean(filename)}'


def upload_review(instance, filename):
    """评审会材料：03_评审会材料/文件名。"""
    return f'{project_folder(instance.project)}/03_评审会材料/{clean(filename)}'


def upload_review_scan(instance, filename):
    """评审意见扫描件挂在 Project 自己身上，instance 就是项目。"""
    return (f'{project_folder(instance)}/03_评审会材料/'
            f'评审意见扫描件_{clean(filename)}')
