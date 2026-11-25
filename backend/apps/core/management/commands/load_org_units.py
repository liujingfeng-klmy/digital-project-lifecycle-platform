"""按 org_data.py 重新导入申报单位名录。

用法：
    python manage.py load_org_units          # 新增或更新，不删已有
    python manage.py load_org_units --prune  # 同时停用名录文件里已删掉的单位

机构调整后改 org_data.py 再跑一次即可，不用写迁移。已有项目里的单位名是
历史事实，不回改。
"""
from django.core.management.base import BaseCommand

from apps.core.models import OrgUnit
from apps.core.org_data import LEGACY_ALIAS, ORG_UNITS


class Command(BaseCommand):
    help = '导入市本级申报单位名录'

    def add_arguments(self, parser):
        parser.add_argument('--prune', action='store_true',
                            help='停用名录文件中已移除的单位（不物理删除）')

    def handle(self, *args, **opts):
        created = updated = 0
        keep = set()
        for name, group, subgroup, order in ORG_UNITS:
            keep.add(name)
            aliases = [k for k, v in LEGACY_ALIAS.items() if v == name and k != name]
            obj, is_new = OrgUnit.objects.update_or_create(
                name=name,
                defaults={'group': group, 'subgroup': subgroup, 'order': order,
                          'active': True, 'aliases': aliases},
            )
            created += is_new
            updated += (not is_new)

        pruned = 0
        if opts['prune']:
            pruned = OrgUnit.objects.exclude(name__in=keep).update(active=False)

        self.stdout.write(self.style.SUCCESS(
            f'名录导入完成：新增 {created}，更新 {updated}，停用 {pruned}'))
