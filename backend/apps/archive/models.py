"""历史档案库。

与项目台账、预审流程完全解耦：这里只负责「记住过去批过什么」，
供新申报项目做重复建设比对。档案不进入流转，不出具任何审批结论。
"""
from django.conf import settings
from django.db import models


class ArchiveProject(models.Model):
    """历史项目。一个项目可以挂多份材料（建议书、可研、概算、批复）。"""

    class Status(models.TextChoices):
        IN_USE = 'in_use', '在用'
        BUILDING = 'building', '建设中'
        ACCEPTED = 'accepted', '已验收'
        OFFLINE = 'offline', '已停用'
        UNKNOWN = 'unknown', '未知'

    name = models.CharField('项目名称', max_length=200, db_index=True)
    unit = models.CharField('建设单位', max_length=120, blank=True)
    year = models.IntegerField('立项年度', null=True, blank=True, db_index=True)
    # 年度是怎么来的。原来只有值没有来源，判错了也看不出来、改对了下次重解析
    # 又会被覆盖回去。有来源才能做到「人工改过的不再自动覆盖」
    year_source = models.CharField('年度来源', max_length=12, blank=True, default='',
                                   choices=[
                                       ('hint', '导入时指定'),
                                       ('label', '封面申报日期'),
                                       ('filename', '文件名'),
                                       ('frequency', '正文高频年份'),
                                       ('manual', '人工修改'),
                                       ('legacy', '历史数据未记录'),
                                   ])
    investment = models.DecimalField('总投资(万元)', max_digits=12, decimal_places=2,
                                     null=True, blank=True)
    category = models.CharField('业务领域', max_length=64, blank=True, db_index=True)
    status = models.CharField('运行状态', max_length=16, choices=Status.choices,
                              default=Status.UNKNOWN)
    note = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+',
                                   verbose_name='录入人')
    created = models.DateTimeField('录入时间', auto_now_add=True)
    updated = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '历史项目'
        verbose_name_plural = '历史项目'
        ordering = ['-year', 'name']
        indexes = [models.Index(fields=['name', 'unit'])]

    def __str__(self):
        return self.name

    @property
    def file_count(self):
        return self.files.count()


class ArchiveFile(models.Model):
    """一份历史材料及其解析结果。

    text / sections / grams 是解析产物，导入时一次生成，比对时直接读，
    不必每次重新解 PDF —— 这是比对能在几秒内完成的前提。
    """

    class Status(models.TextChoices):
        PENDING = 'pending', '待解析'
        PARSING = 'parsing', '解析中'
        READY = 'ready', '已就绪'
        FAILED = 'failed', '解析失败'

    class DocType(models.TextChoices):
        PROPOSAL = 'proposal', '项目建议书'
        FEASIBILITY = 'feasibility', '可行性研究报告'
        BUDGET = 'budget', '投资概算'
        APPROVAL = 'approval', '批复文件'
        OTHER = 'other', '其他材料'

    project = models.ForeignKey(ArchiveProject, on_delete=models.CASCADE,
                                related_name='files', verbose_name='所属项目')
    file = models.FileField('原件', upload_to='archive/%Y%m/', max_length=300)
    filename = models.CharField('文件名', max_length=255)
    size = models.BigIntegerField('大小(字节)', default=0)
    sha256 = models.CharField('内容指纹', max_length=64, db_index=True, blank=True)
    text_hash = models.CharField('正文指纹', max_length=64, db_index=True, blank=True)
    doc_type = models.CharField('材料类型', max_length=16, choices=DocType.choices,
                                default=DocType.FEASIBILITY)

    status = models.CharField('解析状态', max_length=16, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    error = models.TextField('失败原因', blank=True)

    page_count = models.IntegerField('页数', default=0)
    char_count = models.IntegerField('字数', default=0)
    text = models.TextField('正文', blank=True)
    sections = models.JSONField('章节', default=list, blank=True)
    grams = models.JSONField('词频指纹', default=dict, blank=True)
    functions = models.JSONField('功能项', default=list, blank=True)

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+',
                                    verbose_name='上传人')
    created = models.DateTimeField('上传时间', auto_now_add=True)

    class Meta:
        verbose_name = '历史材料'
        verbose_name_plural = '历史材料'
        ordering = ['-created']
        indexes = [models.Index(fields=['sha256'])]

    def __str__(self):
        return self.filename


class CompareReview(models.Model):
    """专家对一次重复建设比对的核定。

    系统只负责筛疑点，这一张表负责装人的判断：谁、什么时候、
    看了哪两份材料、认定构成还是不构成重复、依据是什么。

    为什么要单独一张表而不是在项目上记一个字段：
      1. 同一份申报材料可能被不同专家在不同轮次核定，结论可能变，留痕要求全部保留；
      2. 比对发生在流程之外（申报时预筛、评审时复核），不能绑死在项目记录上；
      3. 将来审计问「这个项目为什么判不重复」，要能调出当次看到的是哪几条疑点。
    """

    class Verdict(models.TextChoices):
        DUPLICATE = 'duplicate', '构成重复建设'
        NOT_DUPLICATE = 'not_duplicate', '不构成重复建设'
        NEED_MORE = 'need_more', '需补材料后再定'

    # 待核定的申报材料：用正文指纹关联，不落库也能留痕
    query_hash = models.CharField('材料正文指纹', max_length=64, db_index=True)
    query_name = models.CharField('申报项目名称', max_length=200, blank=True)
    query_unit = models.CharField('申报单位', max_length=120, blank=True)
    query_year = models.IntegerField('申报年度', null=True, blank=True)

    project = models.ForeignKey(ArchiveProject, on_delete=models.PROTECT,
                                related_name='reviews', verbose_name='比对的既有项目')
    findings = models.JSONField('疑点快照', default=list, blank=True)
    verdict = models.CharField('核定结论', max_length=16, choices=Verdict.choices)
    basis = models.TextField('核定依据', blank=True)

    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='+',
                                 verbose_name='核定人')
    reviewer_name = models.CharField('核定人姓名', max_length=60, blank=True)
    created = models.DateTimeField('核定时间', auto_now_add=True)

    class Meta:
        verbose_name = '重复建设核定'
        verbose_name_plural = '重复建设核定'
        ordering = ['-created']
        indexes = [models.Index(fields=['query_hash', 'project'])]

    def __str__(self):
        return f'{self.query_name or self.query_hash[:8]} → {self.project.name}: {self.get_verdict_display()}'
