"""把历史文件从「按年月」搬到「按项目」。

老结构 media/{project,generated,review}/202611/xxx，新结构
media/projects/编号_项目名/{01_申报材料/环节, 02_生成文书, 03_评审会材料}/。
老文件不会自己挪窝，得跑一次这个命令：

    manage.py organize_media            # 只看清单，不动
    manage.py organize_media --apply    # 真搬，并更新数据库里的路径

搬之前先备份 media/ 与 data/db.sqlite3 —— 搬完项目目录就变了，
文件按记录一条条挪，中途断电会出现「记录指向新路径、文件还在老位置」。
可以重跑，已经在新路径下的记录会被跳过。
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.project import storage
from apps.project.models import Project, ProjectDocument, ProjectFile, ReviewFile


def _free(path: Path) -> Path:
    """目标已存在就加序号，绝不覆盖 —— 同名文件在补正里很常见。"""
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 1
    while True:
        i += 1
        cand = path.with_name(f'{stem}_v{i}{suffix}')
        if not cand.exists():
            return cand


class Command(BaseCommand):
    help = '把 media/ 下的历史文件按项目归到一个文件夹（默认只报告）'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='真正移动文件并更新数据库（默认只列出）')

    def handle(self, *args, **options):
        apply_it = options['apply']
        root = Path(settings.MEDIA_ROOT)
        plan = []          # (记录, 旧相对路径, 新相对路径)
        missing = []       # 记录指向的文件已经不在了

        def plan_one(rec, new_name, label, field='file'):
            old = getattr(rec, field).name if getattr(rec, field) else ''
            if not old or old == new_name:
                return
            if old.startswith('projects/'):
                return  # 已经搬过
            src = root / old
            if not src.exists():
                missing.append((label, old))
                return
            plan.append((rec, old, new_name, label, field))

        for f in ProjectFile.objects.select_related('project').iterator():
            stage = storage.stage_folder(f.stage)
            new = (f'{storage.project_folder(f.project)}/01_申报材料/{stage}/'
                   f'{storage.clean(f.file.name.rsplit("/", 1)[-1] if f.file else "")}')
            plan_one(f, new, f'申报材料#{f.id}')

        for d in ProjectDocument.objects.select_related('project').iterator():
            name = storage.clean(d.file.name.rsplit('/', 1)[-1]) if d.file else ''
            new = f'{storage.project_folder(d.project)}/02_生成文书/{name}'
            plan_one(d, new, f'生成文书#{d.id}')

        for r in ReviewFile.objects.select_related('project').iterator():
            name = storage.clean(r.file.name.rsplit('/', 1)[-1]) if r.file else ''
            new = f'{storage.project_folder(r.project)}/03_评审会材料/{name}'
            plan_one(r, new, f'评审会材料#{r.id}')

        for p in Project.objects.exclude(review_scan='').iterator():
            if not p.review_scan:
                continue
            raw = p.review_scan.name.rsplit('/', 1)[-1]
            new = (f'{storage.project_folder(p)}/03_评审会材料/'
                   f'评审意见扫描件_{storage.clean(raw)}')
            plan_one(p, new, f'评审扫描件 项目{p.code}', field='review_scan')

        self.stdout.write(f'待搬 {len(plan)} 个文件，'
                          f'已在原处缺失 {len(missing)} 条记录')
        for rec, old, new, label, _field in plan[:40]:
            self.stdout.write(f'  {label}\n    {old}\n → {new}')
        if len(plan) > 40:
            self.stdout.write(f'  …… 其余 {len(plan) - 40} 条略')
        for label, old in missing[:10]:
            self.stdout.write(self.style.WARNING(f'  文件不存在：{label} {old}'))

        if not apply_it:
            self.stdout.write(self.style.WARNING(
                '以上仅为清单，未移动任何文件。确认后加 --apply 执行'))
            return

        moved = 0
        for rec, old, new, label, field in plan:
            dst = _free(root / new)
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(root / old), str(dst))
            except OSError as exc:
                self.stdout.write(self.style.ERROR(f'  搬移失败 {label}：{exc}'))
                continue
            getattr(rec, field).name = dst.relative_to(root).as_posix()
            rec.save(update_fields=[field])
            moved += 1

        self.stdout.write(self.style.SUCCESS(f'已搬移并更新 {moved} 条记录'))
        self.stdout.write('空了的老目录（project/generated/review 下按年月的子目录）'
                          '可在确认文件都在新目录后手动删除')
