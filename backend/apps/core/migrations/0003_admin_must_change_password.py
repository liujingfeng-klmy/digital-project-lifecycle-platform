"""管理员也纳入「首次登录须改密」。

init_accounts 里 admin 的口令写在源码中（Admin@123456），原实现偏偏把
admin 排除在强制改密之外，等于最高权限账号的默认口令永久有效。
这里把存量 admin 账号补上标记，与新逻辑一致。
"""
from django.db import migrations


def mark_admins(apps, schema_editor):
    User = apps.get_model('core', 'User')
    User.objects.filter(role='admin').update(must_change_password=True)


def unmark(apps, schema_editor):
    # 不回滚：默认口令已经用过，退回「不必改密」没有意义
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_user_token_version'),
    ]

    operations = [
        migrations.RunPython(mark_admins, unmark),
    ]
