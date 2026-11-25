from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .org_data import GROUP_LABEL, GROUP_ORDER


class Role(models.Model):
    """角色。角色与账号分开维护，一个角色下可以有多个人。

    为什么要有这张表：原来「角色」是写死在代码里的四个字符串，单位里
    初审岗两个人、审核岗三个人，系统分不出来，转出时只能整岗转 ——
    件到了谁桌上靠人喊。改成一张表之后，管理员可以自己建「初审岗（政务）」
    「初审岗（数据）」这类角色，转出时按人指派。

    权限域（scope）是四类固定的办理口径，决定了这个角色在流程里能办哪些
    环节；新建角色只选域，不另配权限 —— 这套系统的权限本来就等于
    「在哪个环节能动手」，多一层自由配置只会配出办不了件的角色。
    """

    class Scope(models.TextChoices):
        APPLICANT = 'applicant', '申报单位'
        REVIEWER = 'reviewer', '初审岗'
        AUDITOR = 'auditor', '审核岗'
        ADMIN = 'admin', '管理员'

    code = models.SlugField('角色代码', max_length=32, unique=True)
    name = models.CharField('角色名称', max_length=32, unique=True)
    scope = models.CharField('权限域', max_length=16, choices=Scope.choices,
                             default=Scope.APPLICANT, db_index=True)
    description = models.CharField('说明', max_length=200, blank=True)
    # 内置角色删除会让历史留痕里的角色名对不上号，只能改名不能删
    builtin = models.BooleanField('内置', default=False)
    active = models.BooleanField('启用', default=True, db_index=True)
    order = models.IntegerField('排序', default=0)
    created = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '角色'
        verbose_name_plural = '角色'
        ordering = ['order', 'id']

    def __str__(self):
        return self.name

    @property
    def scope_label(self):
        return self.get_scope_display()

    @property
    def user_count(self):
        return self.users.filter(is_active=True).count()


class User(AbstractUser):
    """系统用户。角色指向 Role 表，建账号时选角色，不再写死在代码里。

    ``User.Role`` 保留为「权限域」常量（申报 / 初审 / 审核 / 管理），
    ``user.role`` 读出来的是权限域字符串，``user.role_label`` 读出的是
    具体角色名。这样流程判断仍然只认四类域，而界面上能显示「初审岗（政务）」
    这种细分角色。
    """

    class Role(models.TextChoices):
        APPLICANT = 'applicant', '申报单位'
        REVIEWER = 'reviewer', '初审岗'
        AUDITOR = 'auditor', '审核岗'
        ADMIN = 'admin', '管理员'

    name = models.CharField('姓名', max_length=32, blank=True)
    role_ref = models.ForeignKey('Role', null=True, blank=True,
                                 on_delete=models.PROTECT, related_name='users',
                                 verbose_name='角色')
    unit = models.CharField('单位', max_length=64, blank=True)
    phone = models.CharField('手机', max_length=20, blank=True)
    must_change_password = models.BooleanField('首次登录须改密', default=False)
    # 口令版本：改密时 +1。签发的 token 里带 tv，认证时比对，
    # 不等号即视为已吊销 —— JWT 无状态条件下的轻量吊销方案
    token_version = models.IntegerField('口令版本', default=0)
    created = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '用户'
        verbose_name_plural = '用户'
        ordering = ['id']

    def save(self, *args, **kwargs):
        # is_staff / is_superuser 必须与角色域保持单一事实源。原来只在
        # init_accounts 建号时设置过一次，管理员被改成申报单位后仍是
        # superuser，可用原密码登录 /admin/ 改库（绕过业务口的所有限制）。
        # 这里每次保存都按当前角色同步，任何改角色的入口都不会残留后门。
        is_admin = self.role_ref_id and self.role == self.Role.ADMIN
        self.is_staff = bool(is_admin)
        self.is_superuser = bool(is_admin)
        # 调用方若用 update_fields 只更新了别的字段，也要把这两个带上，
        # 否则 update_fields 会跳过这里的同步值
        uf = kwargs.get('update_fields')
        if uf is not None:
            uf = set(uf)
            uf.update({'is_staff', 'is_superuser'})
            kwargs['update_fields'] = uf
        return super().save(*args, **kwargs)

    @property
    def role(self):
        """权限域（applicant / reviewer / auditor / admin）。

        流程、权限、待办全部只认这四类，自定义角色只是同一域下的细分名称，
        所以这里不返回角色代码 —— 返回代码会让每个判断点都得再查一次角色表。
        """
        return self.role_ref.scope if self.role_ref_id else ''

    @role.setter
    def role(self, value):
        """按权限域或角色代码赋值，落到具体角色上。

        保留 setter 是为了不把历史代码和初始化命令全改一遍：
        ``User(role='reviewer')`` 仍然可用，内部落到该域的内置角色。
        """
        if not value:
            self.role_ref = None
            return
        role = (Role.objects.filter(code=str(value)).first()
                or Role.objects.filter(scope=str(value)).order_by('order', 'id').first())
        if role is None:
            raise ValueError(f'角色或权限域不存在：{value}')
        self.role_ref = role

    @property
    def role_code(self):
        return self.role_ref.code if self.role_ref_id else ''

    @property
    def role_label(self):
        return self.role_ref.name if self.role_ref_id else '未分配角色'

    @property
    def display(self):
        return self.name or self.username

    @property
    def is_business_role(self):
        return self.role != self.Role.ADMIN


class OrgUnit(models.Model):
    """市本级申报单位名录。

    申报单、账号的「单位」都从这里选。之前是自由输入，同单位能写出三种写法，
    台账按单位归口统计时对不上号。名录按机构编制口径分组，只收市本级单位，
    垂直管理部门单列一组 —— 它们能登录看数据，但申报入口一般不选。

    ``aliases`` 存该单位的俗写（「市财政局」→「财政局」），只用于把材料里
    抽取到的单位名归一到标准名，不参与界面展示。
    """

    class Group(models.TextChoices):
        PARTY = 'party', GROUP_LABEL['party']
        NPC = 'npc', GROUP_LABEL['npc']
        GOV = 'gov', GROUP_LABEL['gov']
        CPPCC = 'cppcc', GROUP_LABEL['cppcc']
        DISCIPLINE = 'discipline', GROUP_LABEL['discipline']
        MASS = 'mass', GROUP_LABEL['mass']
        DIRECT = 'direct', GROUP_LABEL['direct']
        JUDICIAL = 'judicial', GROUP_LABEL['judicial']
        VERTICAL = 'vertical', GROUP_LABEL['vertical']

    name = models.CharField('单位名称', max_length=100, unique=True)
    group = models.CharField('分类', max_length=16, choices=Group.choices,
                             default=Group.GOV, db_index=True)
    subgroup = models.CharField('二级分类', max_length=40, blank=True)
    order = models.IntegerField('排序', default=0)
    active = models.BooleanField('启用', default=True, db_index=True)
    aliases = models.JSONField('别名', default=list, blank=True)

    class Meta:
        verbose_name = '申报单位名录'
        verbose_name_plural = '申报单位名录'
        ordering = ['order', 'id']

    def __str__(self):
        return self.name

    @property
    def group_label(self):
        return GROUP_LABEL.get(self.group, self.group)

    @property
    def group_order(self):
        return GROUP_ORDER.index(self.group) if self.group in GROUP_ORDER else 99



@receiver([post_save, post_delete], sender=Role)
def _clear_role_name_cache(sender, **kwargs):
    """角色改名 / 删除后，台账上的承办岗位要立刻跟着变。"""
    from apps.project import flow

    flow.clear_role_cache()
