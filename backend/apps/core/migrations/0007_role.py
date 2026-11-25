"""角色与账号拆开：新建 Role 表，把 user.role 的四个固定值落成四条角色记录。

迁移顺序有讲究：先建表、再加外键、再搬数据，最后才删旧字段。
反过来的话，中间任何一步失败都会让账号丢掉角色。
"""

from django.db import migrations, models
import django.db.models.deletion


BUILTIN = [
    # code, name, scope, description, order
    ('applicant', '申报单位', 'applicant', '申报本单位项目、补正材料、反馈意见', 10),
    ('reviewer', '初审岗', 'reviewer', '预审、办评审会、出立项批复、跟进招标与建设', 20),
    ('auditor', '审核岗', 'auditor', '费用评审、财政审核、验收与归档', 30),
    ('admin', '管理员', 'admin', '账号、角色、模板、时限与全部台账', 90),
]


def seed_and_map(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    User = apps.get_model('core', 'User')

    roles = {}
    for code, name, scope, desc, order in BUILTIN:
        role, _ = Role.objects.get_or_create(
            code=code,
            defaults={'name': name, 'scope': scope, 'description': desc,
                      'builtin': True, 'active': True, 'order': order},
        )
        roles[code] = role

    # 历史数据里出现过 expert（评审专家，已取消），归到初审岗，
    # 与 init_accounts 里停用专家账号的处理保持一致
    alias = {'expert': 'reviewer'}
    for user in User.objects.all():
        code = alias.get(getattr(user, 'role', '') or '', getattr(user, 'role', '') or '')
        role = roles.get(code) or roles['applicant']
        user.role_ref_id = role.id
        user.save(update_fields=['role_ref'])


def unseed(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Role.objects.filter(code__in=[r[0] for r in BUILTIN]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_alter_user_role'),
    ]

    operations = [
        migrations.CreateModel(
            name='Role',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('code', models.SlugField(max_length=32, unique=True, verbose_name='角色代码')),
                ('name', models.CharField(max_length=32, unique=True, verbose_name='角色名称')),
                ('scope', models.CharField(
                    choices=[('applicant', '申报单位'), ('reviewer', '初审岗'),
                             ('auditor', '审核岗'), ('admin', '管理员')],
                    db_index=True, default='applicant', max_length=16, verbose_name='权限域')),
                ('description', models.CharField(blank=True, max_length=200, verbose_name='说明')),
                ('builtin', models.BooleanField(default=False, verbose_name='内置')),
                ('active', models.BooleanField(db_index=True, default=True, verbose_name='启用')),
                ('order', models.IntegerField(default=0, verbose_name='排序')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
            ],
            options={
                'verbose_name': '角色',
                'verbose_name_plural': '角色',
                'ordering': ['order', 'id'],
            },
        ),
        migrations.AddField(
            model_name='user',
            name='role_ref',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='users', to='core.role', verbose_name='角色'),
        ),
        migrations.RunPython(seed_and_map, unseed),
        migrations.RemoveField(
            model_name='user',
            name='role',
        ),
    ]
