"""扫描并清理 media/ 下没有被数据库引用的孤儿文件。

孤儿是怎么来的：删除项目/材料时记录删了、磁盘文件没删（本轮已用
post_delete 信号补上），历史遗留的那批需要单独清一次。

默认只报告、不删除。确认清单无误后再加 --delete。
    manage.py clean_orphan_media
    manage.py clean_orphan_media --delete
"""
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.archive.models import ArchiveFile
from apps.project.models import (DocTemplate, Project, ProjectDocument,
                                 ProjectFile, ReviewFile)


def _referenced_paths():
    """所有被数据库引用的物理路径。

    原来只算了申报材料与档案，生成文书、评审会材料、评审扫描件、文书模板
    都没算 —— 跑 --delete 会把《立项批复》《数据归集证明》当孤儿删掉，
    而数据库里记录还在，点开就是 404。宁可多算，不能漏算。
    """
    refs = set()

    def add(field_file):
        if field_file:
            refs.add(os.path.normpath(field_file.path))

    for model in (ProjectFile, ProjectDocument, ReviewFile, DocTemplate):
        for rec in model.objects.all().only('file').iterator():
            add(rec.file)
    for rec in Project.objects.exclude(review_scan='').only('review_scan').iterator():
        add(rec.review_scan)
    for rec in ArchiveFile.objects.exclude(file='').only('file').iterator():
        add(rec.file)
    return refs


class Command(BaseCommand):
    help = '扫描 media/ 下未被数据库引用的文件（默认只报告）'

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true',
                            help='真正删除孤儿文件（默认只列出）')

    def handle(self, *args, **options):
        root = Path(settings.MEDIA_ROOT)
        if not root.exists():
            self.stdout.write('media 目录不存在，无需清理')
            return

        refs = _referenced_paths()
        orphans, total_bytes = [], 0
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                full = os.path.normpath(os.path.join(dirpath, name))
                if full in refs:
                    continue
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                orphans.append((full, size))
                total_bytes += size

        if not orphans:
            self.stdout.write(self.style.SUCCESS('没有孤儿文件'))
            return

        self.stdout.write(f'发现 {len(orphans)} 个孤儿文件，'
                          f'合计 {total_bytes / 1048576:.1f} MB')
        for full, size in orphans[:50]:
            self.stdout.write(f'  {size / 1024:.0f} KB  {full}')
        if len(orphans) > 50:
            self.stdout.write(f'  …… 其余 {len(orphans) - 50} 个略')

        if not options['delete']:
            self.stdout.write(self.style.WARNING(
                '以上仅为报告，未删除任何文件。确认后加 --delete 执行'))
            return

        removed = 0
        for full, _size in orphans:
            try:
                os.remove(full)
                removed += 1
            except OSError as exc:
                self.stdout.write(f'  删除失败 {full}：{exc}')
        self.stdout.write(self.style.SUCCESS(f'已删除 {removed} 个孤儿文件'))
