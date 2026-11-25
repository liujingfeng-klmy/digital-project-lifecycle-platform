"""按当前章节关键词表重算存量档案的章节类别与功能清单。

为什么要单独跑一遍：章节类别、功能清单都是入库时算好存下的。查重口径从
「整篇比」收窄到「只比建设内容章」以后，旧数据里「测评方案」「网络架构」
仍带着「建设内容与方案」的类别，会被当成建设内容继续参与比对。
"""
from django.core.management.base import BaseCommand

from apps.archive import functions, textparse
from apps.archive.models import ArchiveFile


class Command(BaseCommand):
    help = '重算存量档案的章节类别与功能清单（查重口径变化后执行一次）'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='只统计不写库')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        changed = same = 0
        moved = {}
        for rec in ArchiveFile.objects.exclude(sections=[]).iterator():
            old = rec.sections or []
            if not old:
                continue
            new = textparse.retag(old)
            funcs = functions.extract_functions(new)
            for before, after in zip(old, new):
                if before.get('category') != after.get('category'):
                    key = f"{before.get('category')} → {after.get('category')}"
                    moved[key] = moved.get(key, 0) + 1
            if new == old and funcs == (rec.functions or []):
                same += 1
                continue
            changed += 1
            if not dry:
                rec.sections = new
                rec.functions = funcs
                rec.save(update_fields=['sections', 'functions'])

        self.stdout.write(f'{"将调整" if dry else "已调整"} {changed} 份，无变化 {same} 份')
        for k, v in sorted(moved.items(), key=lambda kv: -kv[1])[:12]:
            self.stdout.write(f'  {k}: {v} 章')
