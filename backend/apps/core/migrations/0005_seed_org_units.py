"""导入市本级申报单位名录，并把历史账号/项目里的单位写法归一到标准名。

种子数据取自 apps/core/org_data.py。这里直接引用而不内联一份拷贝，是因为
名录是业务数据而非 schema —— 机构调整后重新导入应以最新文件为准，迁移
只负责「空库时把名录铺上」，后续维护走 admin 或 load_org_units 命令。
"""
from django.db import migrations

LEGACY_UNIT = {
    '市数据管理局': '数据管理局',
    '市行政审批局': '数据管理局',
}


def seed(apps, schema_editor):
    OrgUnit = apps.get_model('core', 'OrgUnit')
    User = apps.get_model('core', 'User')
    from apps.core.org_data import LEGACY_ALIAS, ORG_UNITS

    for name, group, subgroup, order in ORG_UNITS:
        aliases = [k for k, v in LEGACY_ALIAS.items() if v == name and k != name]
        OrgUnit.objects.update_or_create(
            name=name,
            defaults={'group': group, 'subgroup': subgroup,
                      'order': order, 'active': True, 'aliases': aliases},
        )

    # 已存在的账号单位是自由输入的，能对上就改标准名，对不上不动（不猜）
    for user in User.objects.exclude(unit=''):
        target = LEGACY_UNIT.get(user.unit) or LEGACY_ALIAS.get(user.unit)
        if target and OrgUnit.objects.filter(name=target).exists():
            user.unit = target
            user.save(update_fields=['unit'])


def unseed(apps, schema_editor):
    apps.get_model('core', 'OrgUnit').objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [('core', '0004_orgunit')]
    operations = [migrations.RunPython(seed, unseed)]
