"""初始化账号：建一个管理员和三个业务角色的演示账号。

用法：
    .venv/bin/python manage.py init_accounts
可重复执行，已存在的账号跳过。

v0.6 起取消「评审专家」角色：评审会结论由初审岗一人录入票数并上传
签字扫描件，不再为每位专家建账户。历史遗留的 expert 账号会被停用
并转为 reviewer，事件记录里已留的姓名不受影响。
"""
from django.core.management.base import BaseCommand

from apps.core.models import User

ACCOUNTS = [
    # username, name, role, unit, password
    ('admin', '系统管理员', User.Role.ADMIN, '数据管理局', 'Admin@123456'),
    ('shenbao', '张申报', User.Role.APPLICANT, '数据管理局', 'Pm@12345678'),
    ('chushen', '李初审', User.Role.REVIEWER, '数据管理局', 'Pm@12345678'),
    ('shenhe', '赵审核', User.Role.AUDITOR, '数据管理局', 'Pm@12345678'),
]


class Command(BaseCommand):
    help = '初始化系统账号（可重复执行）'

    def handle(self, *args, **options):
        created = []
        for username, name, role, unit, password in ACCOUNTS:
            if User.objects.filter(username=username).exists():
                continue
            u = User(username=username, name=name, role=role, unit=unit,
                     is_staff=(role == User.Role.ADMIN),
                     is_superuser=(role == User.Role.ADMIN))
            u.set_password(password)
            # 包括 admin：初始口令写在源码里，等于公开的秘密，
            # 首次登录必须改掉，否则「首次登录须改密」只是句口号
            u.must_change_password = True
            u.save()
            created.append(f'{username}（{u.role_label}）')

        if created:
            self.stdout.write(self.style.SUCCESS('已创建账号：' + '、'.join(created)))
        else:
            self.stdout.write('账号已存在，未做改动')

        self.stdout.write('初始密码：admin 为 Admin@123456，其余为 Pm@12345678')

