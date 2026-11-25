"""项目申报与全生命周期流转。

一张 Project 走完全过程，阶段只由 flow.transition 推动 —— 页面上没有
「把状态改成 X」这种操作，避免有人绕过流程直接改库或改表单。

留痕单独一张 ProjectEvent：谁、什么角色、在什么时候、从哪个阶段推到哪个阶段、
填了什么意见。事件只增不改，删除项目时随之级联删除（项目本身不存在了，
孤立事件没有意义；真要防删，应在数据库层做软删除，这一版不做）。
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from . import storage


class Project(models.Model):
    """一个申报项目。"""

    class Stage(models.TextChoices):
        DRAFT = 'draft', '申报中'
        PRECHECK = 'precheck', '预审中'
        SUPPLEMENT = 'supplement', '待补正'
        EXPERT_REVIEW = 'expert_review', '评审中'
        # 评审会给「修改后通过」时用：原则上同意，但要按意见改完材料
        # 才能进费用评审。这一步不该退回预审重来一遍，也不要再上会
        REVIEW_SUPPLEMENT = 'review_supplement', '评审后补正'
        # 正式环节里没有「审核」，而是费用评审：把专家评审后的可研交财评机构，
        # 由财评机构出具评估意见。系统里由审核岗登记委托与意见
        COST_REVIEW = 'cost_review', '费用评审'
        # 费用评审不只有一轮：评估意见出来后请申报单位反馈（有异议交证据、
        # 没异议可直接确认），再回到费用评审重评。这条路不能借用预审补正 ——
        # 走了预审补正就要重新预审、重新上会，等于让同一批人把结论再表决一次。
        # 名称用「征求反馈」而非「退回补正」：多数情况单位只需确认或补一份依据，
        # 说「退回」像在问责，也容易让人误以为材料被否掉
        COST_SUPPLEMENT = 'cost_supplement', '征求反馈'
        # 费用评审通过后，先由财政审定资金安排；没钱的进储备库，不硬推招标
        FISCAL_REVIEW = 'fiscal_review', '财政审核'
        # 财政同意后出立项批复，批复出具才算立项完成
        APPROVAL = 'approval', '立项'
        TENDERING = 'tendering', '待招标'
        BUILDING = 'building', '建设中'
        TRIAL = 'trial', '试运行'
        ACCEPTANCE = 'acceptance', '待验收'
        ACCEPTED = 'accepted', '已验收'
        # 财政无资金安排的进储备库：不是终态，等有资金时可再纳入
        RESERVE = 'reserve', '项目储备库'
        REJECTED = 'rejected', '不予通过'

    class ReviewResult(models.TextChoices):
        # 三档口径跟票数一致：通过 / 修改后通过 / 不通过。
        # 界面、事件、档案里都只出现这三个词，别再混用「同意」「不予通过」
        PASS = 'pass', '通过'
        MODIFIED = 'modified_pass', '修改后通过'
        REJECT = 'reject', '不通过'

    class AuditResult(models.TextChoices):
        PASS = 'pass', '费用评审通过'
        REJECT = 'reject', '费用评审不通过'

    class FiscalResult(models.TextChoices):
        PASS = 'pass', '财政审核同意'
        REJECT = 'reject', '财政无资金安排'

    code = models.CharField('项目编号', max_length=32, unique=True, blank=True)
    name = models.CharField('项目名称', max_length=200, db_index=True)
    applicant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name='projects', verbose_name='申报账号')
    unit = models.CharField('申报单位', max_length=120)
    # 联系人 / 手机号：通知要落到人。单位账号是公用的（办公室里一台机器），
    # 只写单位名，件到了、退回了、评审结论出来了，短信没人收。
    # 每件单独填：同一单位不同项目常常是不同经办人
    contact_name = models.CharField('联系人', max_length=40, blank=True, default='')
    contact_phone = models.CharField('联系电话', max_length=20, blank=True, default='')
    year = models.IntegerField('申报年度', null=True, blank=True, db_index=True)
    category = models.CharField('业务领域', max_length=64, blank=True, db_index=True)
    investment = models.DecimalField('总投资(万元)', max_digits=12, decimal_places=2,
                                     null=True, blank=True)
    # 核定预算：立项批复真正要写的数。可研里的投资是申报口径，财评会核减；
    # 财政审核又可能再调整。所以单落一个字段：费用评审通过时按评估结论写入，
    # 财政审核通过时可改，批复一律取这个数（没有才回落到可研投资）
    budget_amount = models.DecimalField('核定预算(万元)', max_digits=12, decimal_places=2,
                                        null=True, blank=True)
    duration_months = models.IntegerField('建设周期(月)', null=True, blank=True)

    summary = models.TextField('建设内容', blank=True)
    necessity = models.TextField('建设必要性', blank=True)
    basis = models.TextField('政策依据', blank=True)

    # 涉敏标记：勾选后材料不出网，只跑规则预审。
    # 之前设置页有这个开关、方案也承诺了，但库里没字段、代码不读，等于假开关
    sensitive = models.BooleanField('涉敏材料', default=False, db_index=True)

    # 24 而不是 16：review_supplement 有 17 个字符，卡在 16 上迁移直接报错
    stage = models.CharField('当前阶段', max_length=24, choices=Stage.choices,
                             default=Stage.DRAFT, db_index=True)
    # 当前承办人：转出时由经办人指定。role 是「哪个岗位办」，handler 是「谁办」——
    # 一个岗位上有几个人时，只按岗位转出，件到了谁桌上只能靠喊；指定到人之后，
    # 待办才真的是「我的」。没指定时为空，该岗位所有人都能接（休假、换人兜底）
    handler = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='handling',
                                verbose_name='当前承办人')
    handler_role = models.CharField('当前办理角色', max_length=32, blank=True,
                                    db_index=True)
    round = models.IntegerField('补正轮次', default=0)

    review_result = models.CharField('评审结论', max_length=16,
                                     choices=ReviewResult.choices, blank=True)
    review_note = models.TextField('评审意见', blank=True)
    # 评审会票数。专家不在系统里建账号，会务按签字表录入三档票数，
    # 结论由票数算出来 —— 手输结论等于给了一个绕开票数的口子
    review_agree = models.IntegerField('通过票数', default=0)
    review_modified = models.IntegerField('修改后通过票数', default=0)
    review_reject = models.IntegerField('不通过票数', default=0)
    concluded_at = models.DateTimeField('评审结论时间', null=True, blank=True)
    audit_result = models.CharField('费用评审结论', max_length=16,
                                    choices=AuditResult.choices, blank=True)
    audit_note = models.TextField('费用评审意见', blank=True)
    audited_at = models.DateTimeField('费用评审时间', null=True, blank=True)

    # 财政审核：审的是「有没有钱、给多少」，与费用评审（合不合理）分开留痕
    fiscal_result = models.CharField('财政审核结论', max_length=16,
                                     choices=FiscalResult.choices, blank=True)
    fiscal_note = models.TextField('财政审核意见', blank=True)
    fiscal_at = models.DateTimeField('财政审核时间', null=True, blank=True)
    # 立项：批复文号与出具时间。批复正文由模板生成，正文里带文号
    approval_no = models.CharField('批复文号', max_length=60, blank=True)
    approval_note = models.TextField('立项说明', blank=True)
    approval_at = models.DateTimeField('立项时间', null=True, blank=True)

    # 评审会签字意见的扫描件（整场一份）。会上评完、会后收表扫描，
    # 这是评审结论的原始凭证：只存几个电子数字，事后证明不了谁签了字
    review_scan = models.FileField('评审意见扫描件', upload_to=storage.upload_review_scan,
                                   max_length=300, blank=True)
    review_scan_name = models.CharField('扫描件文件名', max_length=200, blank=True)

    dup_check = models.JSONField('重复建设筛查', default=dict, blank=True)

    # 试运行由建设单位执行：他们把运行情况与「归集了哪些数据」报上来，
    # 待验收时我局据此出具《数据归集证明》。这两段文字是证明的正文来源，
    # 也是日后说清「凭什么说数据已归集」的原始依据，所以落在项目上，
    # 不塞进流转留痕的备注里（备注只留一句话，写不下清单）
    trial_summary = models.TextField('试运行情况', blank=True, default='')
    data_collect_scope = models.TextField('数据归集情况', blank=True, default='')
    acceptance_applied_at = models.DateTimeField('申请验收时间', null=True, blank=True)

    submitted_at = models.DateTimeField('首次提交时间', null=True, blank=True)
    stage_at = models.DateTimeField('进入当前阶段时间', default=timezone.now)
    created = models.DateTimeField('创建时间', auto_now_add=True)
    updated = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '申报项目'
        verbose_name_plural = '申报项目'
        ordering = ['-created']
        indexes = [models.Index(fields=['stage', 'handler_role']),
                   models.Index(fields=['unit', 'year'])]

    def __str__(self):
        return f'{self.code} {self.name}'

    # ------------------------------------------------------------ 展示辅助
    @property
    def stage_label(self):
        return self.get_stage_display()

    @property
    def handler_name(self):
        return self.handler.display if self.handler_id else ''

    @property
    def handler_label(self):
        """承办显示：有人写「张三（初审岗）」，没人写岗位名。

        没指定承办人时不能显示「已结束」—— 那会让人以为流程停了，
        实际是「该岗位谁都能接」，所以要退回岗位名。
        """
        if self.handler_id:
            return f'{self.handler.display}（{self.handler.role_label}）'
        from .flow import handler_role_of, role_name

        code = self.handler_role or handler_role_of(self.stage)
        return role_name(code) if code else ''

    @property
    def review_result_label(self):
        return self.get_review_result_display() if self.review_result else ''

    @property
    def audit_result_label(self):
        return self.get_audit_result_display() if self.audit_result else ''

    @property
    def fiscal_result_label(self):
        return self.get_fiscal_result_display() if self.fiscal_result else ''

    @property
    def is_closed(self):
        return self.stage in (self.Stage.ACCEPTED, self.Stage.REJECTED)

    @property
    def days_in_stage(self):
        if not self.stage_at:
            return 0
        return max((timezone.now() - self.stage_at).days, 0)

    @property
    def investment_wan(self):
        return float(self.investment) if self.investment is not None else None

    @property
    def budget_wan(self):
        """批复用的数：财政/财评核定的预算优先，没有才用可研申报投资。"""
        v = self.budget_amount if self.budget_amount is not None else self.investment
        return float(v) if v is not None else None

    def set_stage(self, stage, fields=None):
        """阶段变更统一入口：同时刷新办理角色与进入时间。"""
        from .flow import handler_role_of

        self.stage = stage
        self.stage_at = timezone.now()
        self.handler_role = handler_role_of(stage)
        update = ['stage', 'stage_at', 'handler_role', 'updated']
        for k, v in (fields or {}).items():
            setattr(self, k, v)
            update.append(k)
        self.save(update_fields=sorted(set(update)))


class ProjectFile(models.Model):
    """申报材料。解析产物与历史档案同一套算法（textparse / functions / grams）。"""

    class Status(models.TextChoices):
        PENDING = 'pending', '待解析'
        PARSING = 'parsing', '解析中'
        READY = 'ready', '已就绪'
        FAILED = 'failed', '解析失败'

    class DocType(models.TextChoices):
        PROPOSAL = 'proposal', '项目建议书'
        FEASIBILITY = 'feasibility', '可行性研究报告'
        BUDGET = 'budget', '投资概算'
        APPROVAL = 'approval', '批复或依据文件'
        # 费用评审退回后单位提交的佐证：报价单、市场询价、同类项目合同等。
        # 单独一类才能和申报正本分开看，也便于审核岗对照异议逐项核
        EVIDENCE = 'evidence', '异议证据'
        # 第三方财评机构出具的评估报告。费用评审是机构做的，结论写在报告里，
        # 审核岗不替机构写意见 —— 报告本身才是这一步的成果物，单列一类便于归档与递送财政
        COST_REPORT = 'cost_report', '费用评估报告'
        # 数据归集证明：验收前由我局出具。没有归集就验收，等于给「建了不用、
        # 数据另起一套」的项发放行条，所以单列一类并作为验收的前置条件
        DATA_COLLECT = 'data_collect', '数据归集证明'
        # 专家验收意见：验收会的结论，报我局备案即可。合同、中标通知书、
        # 建设过程档案都由建设单位自己保管，本系统不为其他单位承担这些责任
        ACCEPT_OPINION = 'accept_opinion', '专家验收意见'
        OTHER = 'other', '其他材料'

    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='files', verbose_name='所属项目')
    file = models.FileField('原件', upload_to=storage.upload_material, max_length=300)
    filename = models.CharField('文件名', max_length=255)
    doc_type = models.CharField('材料类型', max_length=16, choices=DocType.choices,
                                default=DocType.FEASIBILITY)
    size = models.BigIntegerField('大小(字节)', default=0)
    sha256 = models.CharField('内容指纹', max_length=64, db_index=True, blank=True)

    status = models.CharField('解析状态', max_length=16, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    # 补正会产生第二份同名可研：只靠文件名分不出哪份是改过的。
    # 版本按「同项目 + 同类型」递增，旧版留档不删 —— 审计要能回溯改了什么
    round = models.IntegerField('所属轮次', default=0, db_index=True)
    # 上传时所处的环节：详情页「项目资料」按环节分文件夹就靠它，
    # 没有它只能按轮次猜，而一轮里可能跨「待补正」和「费用评审」两个环节
    stage = models.CharField('上传时环节', max_length=24, blank=True, default='')
    version = models.IntegerField('版本号', default=1)
    is_current = models.BooleanField('当前版本', default=True, db_index=True)
    error = models.TextField('失败原因', blank=True)
    page_count = models.IntegerField('页数', default=0)
    char_count = models.IntegerField('字数', default=0)
    text = models.TextField('正文', blank=True)
    sections = models.JSONField('章节', default=list, blank=True)
    # 按「第X章」聚合后的整章正文。sections 是给功能抽取和查重用的细粒度小节，
    # chapters 是给 AI 通读用的整章 —— 两者粒度不同，不能互相替代
    chapters = models.JSONField('整章', default=list, blank=True)
    grams = models.JSONField('词频指纹', default=dict, blank=True)
    functions = models.JSONField('功能项', default=list, blank=True)

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+',
                                    verbose_name='上传人')
    created = models.DateTimeField('上传时间', auto_now_add=True)

    class Meta:
        verbose_name = '申报材料'
        verbose_name_plural = '申报材料'
        ordering = ['id']

    def __str__(self):
        return self.filename


class ReviewFile(models.Model):
    """评审会材料：每位专家的意见表、汇总意见表、其他佐证。

    原来只留一个 review_scan 字段，结果一次评审会七八张签字表只能拼成
    一个 PDF 上传，或者干脆只传汇总表、专家表躺在会务的抽屉里。判定
    依据不齐，事后追责就只能凭记忆 —— 所以按「一份文件一条记录」存，
    再标上是哪位专家/哪类表（不建专家账号，只记表，不记人）。
    """

    class Kind(models.TextChoices):
        EXPERT = 'expert', '专家意见表'
        SUMMARY = 'summary', '汇总意见表'
        OTHER = 'other', '其他材料'

    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='review_files', verbose_name='所属项目')
    file = models.FileField('文件', upload_to=storage.upload_review, max_length=300)
    name = models.CharField('文件名', max_length=200)
    kind = models.CharField('材料类型', max_length=16, choices=Kind.choices,
                            default=Kind.EXPERT)
    size = models.BigIntegerField('大小(字节)', default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+',
                                    verbose_name='上传人')
    created = models.DateTimeField('上传时间', auto_now_add=True)

    class Meta:
        verbose_name = '评审会材料'
        verbose_name_plural = '评审会材料'
        ordering = ['id']

    def __str__(self):
        return f'{self.get_kind_display()}：{self.name}'


class PrecheckReport(models.Model):
    """一次预审的结果。level=rule 为一级规则预审，level=ai 为二级 AI 深度审查。

    每次执行都新增一条，不覆盖：第一轮退回补正后重报，两轮的预审结果
    都要留着，否则说不清「当时是按什么退的」。
    """

    class Level(models.TextChoices):
        RULE = 'rule', '规则预审'
        AI = 'ai', 'AI 深度审查'

    class Verdict(models.TextChoices):
        PASS = 'pass', '未发现问题'
        ATTENTION = 'attention', '有需说明项'
        BLOCK = 'block', '有必须补正项'

    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='reports', verbose_name='项目')
    round = models.IntegerField('第几轮', default=0)
    level = models.CharField('级别', max_length=8, choices=Level.choices)
    verdict = models.CharField('结论', max_length=12, choices=Verdict.choices)
    counts = models.JSONField('统计', default=dict, blank=True)
    items = models.JSONField('检查项', default=list, blank=True)
    findings = models.JSONField('AI 问题清单', default=list, blank=True)
    note = models.TextField('说明', blank=True)
    llm_info = models.JSONField('模型信息', default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+',
                                   verbose_name='执行人')
    created = models.DateTimeField('执行时间', auto_now_add=True)

    class Meta:
        verbose_name = '预审报告'
        verbose_name_plural = '预审报告'
        ordering = ['-created']
        indexes = [models.Index(fields=['project', 'level', 'round'])]

    def __str__(self):
        return f'{self.project.code} 第{self.round}轮 {self.get_level_display()}'


class SupplementNotice(models.Model):
    """退回补正单。申报单位照着这张单子改，改完在系统里写回复。"""

    class Status(models.TextChoices):
        OPEN = 'open', '待补正'
        RESOLVED = 'resolved', '已补正'

    class Kind(models.TextChoices):
        PRECHECK = 'precheck', '预审退回'
        AUDIT = 'audit', '征求反馈'
        REVIEW = 'review', '评审会要求修改'

    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='notices', verbose_name='项目')
    # 补正单的来源决定改完之后回哪：预审退回回预审，评审会要求修改直接交审核
    kind = models.CharField('来源', max_length=10, choices=Kind.choices,
                            default=Kind.PRECHECK)
    round = models.IntegerField('第几轮', default=1)
    items = models.JSONField('补正事项', default=list, blank=True)
    note = models.TextField('补充说明', blank=True)
    deadline = models.DateField('补正期限', null=True, blank=True)
    status = models.CharField('状态', max_length=10, choices=Status.choices,
                              default=Status.OPEN, db_index=True)
    reply = models.TextField('补正回复', blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name='+',
                                  verbose_name='发出人')
    issued_at = models.DateTimeField('发出时间', auto_now_add=True)
    resolved_at = models.DateTimeField('补正时间', null=True, blank=True)

    class Meta:
        verbose_name = '补正单'
        verbose_name_plural = '补正单'
        ordering = ['-issued_at']
        indexes = [models.Index(fields=['project', 'status'])]

    def __str__(self):
        return f'{self.project.code} 第{self.round}轮补正单'


class ProjectEvent(models.Model):
    """流转留痕。只增不改，页面上的时间轴就是按这张表渲染。"""

    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='events', verbose_name='项目')
    action = models.CharField('动作', max_length=32, db_index=True)
    action_label = models.CharField('动作名称', max_length=40)
    from_stage = models.CharField('原阶段', max_length=24, blank=True)
    to_stage = models.CharField('新阶段', max_length=24, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='+',
                              verbose_name='操作人')
    actor_name = models.CharField('操作人姓名', max_length=60, blank=True)
    actor_role = models.CharField('操作人角色', max_length=16, blank=True)
    actor_role_label = models.CharField('角色名称', max_length=20, blank=True)
    comment = models.TextField('意见', blank=True)
    payload = models.JSONField('附加信息', default=dict, blank=True)
    created = models.DateTimeField('时间', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = '流转记录'
        verbose_name_plural = '流转记录'
        ordering = ['created', 'id']
        indexes = [models.Index(fields=['project', 'created'])]

    def __str__(self):
        return f'{self.action_label} → {self.to_stage}'


class ActionDraft(models.Model):
    """办理草稿：填到一半先存下来，件留在「在办箱」里。

    为什么单独一张表、不塞进 ProjectEvent：留痕只增不改，而且别人能看到；
    草稿是「我自己的半成品」，随时覆盖、可以丢弃，也不该算一次流转动作。

    一件在同一环节只留一份草稿：换了个动作重选，就覆盖前一份 ——
    一个人在同一环节同时进行两个动作的场面不存在，多存几份只会让人分不清
    哪份是最后要交的。
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='drafts', verbose_name='项目')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='action_drafts', verbose_name='保存人')
    stage = models.CharField('环节', max_length=24)
    action_key = models.CharField('动作', max_length=32)
    payload = models.JSONField('已填内容', default=dict, blank=True)
    created = models.DateTimeField('首次保存', auto_now_add=True)
    updated = models.DateTimeField('最近保存', auto_now=True, db_index=True)

    class Meta:
        verbose_name = '办理草稿'
        verbose_name_plural = '办理草稿'
        # 一件、一人、一环节只有一份，重复保存走 update_or_create
        constraints = [models.UniqueConstraint(fields=['project', 'user', 'stage'],
                                               name='uniq_draft_per_stage')]
        indexes = [models.Index(fields=['user', 'stage'])]

    def __str__(self):
        return f'{self.project.code} {self.user.display} 草稿'


class ProjectCodeSequence(models.Model):
    """按年度发号的项目编号序列。

    原来用「当前最大号 +1」：删掉 XM2026-009 之后新建又会拿到 009，
    历史导出、审计记录、纸质台账会撞号。改成独立序列，只增不回退，
    删项目不会让号被复用。
    """

    year = models.IntegerField('年度', unique=True)
    last_no = models.IntegerField('已发到', default=0)

    class Meta:
        verbose_name = '项目编号序列'
        verbose_name_plural = '项目编号序列'

    def __str__(self):
        return f'XM{self.year}-{self.last_no:03d}'


class DocTemplate(models.Model):
    """模板库：管理员维护、只对管理员可见。

    「挂到哪个环节」用 stage 表达，生成文书时按 kind 取当前启用的一份。
    内置的三份模板（预审修正说明、费用评审任务书、立项批复）以 packaged
    文件兜底：管理员没上传新版时用打包模板，上传后自动改用上传件。
    正文里的可替换字段统一用 {{project_name}} 这种双花括号占位符。
    """

    class Kind(models.TextChoices):
        PRECHECK_NOTICE = 'precheck_notice', '预审修正说明'
        COST_REVIEW_TASK = 'cost_review_task', '费用评审任务书'
        APPROVAL_REPLY = 'approval_reply', '立项批复'
        # 数据归集证明：建设单位的试运行情况与归集说明报上来之后，
        # 由我局按模板出具，发给建设单位；不是让他们自己传一份扫描件
        DATA_COLLECT = 'data_collect', '数据归集证明'
        OTHER = 'other', '其他材料模板'

    name = models.CharField('模板名称', max_length=120)
    kind = models.CharField('文书类型', max_length=32, choices=Kind.choices,
                            default=Kind.OTHER, db_index=True)
    stage = models.CharField('适用环节', max_length=24, choices=Project.Stage.choices,
                             blank=True, db_index=True)
    description = models.CharField('说明', max_length=200, blank=True)
    file = models.FileField('模板文件', upload_to='templates/%Y%m/', max_length=300,
                            blank=True)
    filename = models.CharField('文件名', max_length=200, blank=True)
    # 内置模板标识：没有上传文件时，生成器用后端打包的同名 docx
    builtin_key = models.CharField('内置标识', max_length=40, blank=True)
    active = models.BooleanField('启用', default=True, db_index=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+',
                                    verbose_name='维护人')
    created = models.DateTimeField('创建时间', auto_now_add=True)
    updated = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '文书模板'
        verbose_name_plural = '文书模板'
        ordering = ['kind', '-updated']

    def __str__(self):
        return self.name

    @property
    def stage_label(self):
        return self.get_stage_display() if self.stage else '通用'


class ProjectDocument(models.Model):
    """系统按模板生成的文书（预审修正说明 / 费用评审任务书 / 立项批复）。

    每次生成都留一条：模板改过之后，要能说清当时发出去的是哪一版、
    选了哪些意见。文件落 media/generated/，随项目归档。
    """

    Kind = DocTemplate.Kind

    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='documents', verbose_name='项目')
    kind = models.CharField('文书类型', max_length=32, choices=DocTemplate.Kind.choices)
    name = models.CharField('文件名', max_length=200)
    stage = models.CharField('生成时阶段', max_length=24, blank=True)
    round = models.IntegerField('生成时轮次', default=0)
    file = models.FileField('文件', upload_to=storage.upload_generated, max_length=300)
    template = models.ForeignKey(DocTemplate, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='+',
                                 verbose_name='所用模板')
    meta = models.JSONField('生成参数', default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+',
                                   verbose_name='生成人')
    created = models.DateTimeField('生成时间', auto_now_add=True)

    class Meta:
        verbose_name = '生成文书'
        verbose_name_plural = '生成文书'
        ordering = ['-created']

    def __str__(self):
        return self.name


class TodoAck(models.Model):
    """待办已读标记。

    侧栏数字原来只按「项目是否还在我这个阶段」算，点进去看完、还没办，
    数字照样挂着，看起来像没点过。这里按「人 + 项目 + 阶段 + 轮次」记一条：
    进过详情就减一；项目换阶段或开新一轮，旧标记失效，重新计入待办。
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='todo_acks', verbose_name='用户')
    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='todo_acks', verbose_name='项目')
    stage = models.CharField('阶段', max_length=24)
    round = models.IntegerField('轮次', default=0)
    created = models.DateTimeField('查看时间', auto_now_add=True)

    class Meta:
        verbose_name = '待办已读'
        verbose_name_plural = '待办已读'
        unique_together = [('user', 'project', 'stage', 'round')]
        indexes = [models.Index(fields=['user', 'stage'])]

    def __str__(self):
        return f'{self.user_id}-{self.project_id}-{self.stage}'


class StageSla(models.Model):
    """各环节的审查时限（自然日），由管理员在「系统设置」里调整。

    为什么落库而不是写死在代码里：时限是管理要求，不是业务规则 ——
    今年要求 5 个工作日、明年压到 3 个，改一次代码发一次版不现实。
    表里没有记录的环节回落到 DEFAULT_SLA（flow.SLA_DEFAULTS），
    这样新增环节不会因为没人配时限就变成「不限时」。
    """

    stage = models.CharField('环节', max_length=24, choices=Project.Stage.choices,
                             unique=True)
    days = models.IntegerField('时限(自然日)', default=7)
    # 该环节由哪个岗位办。留空表示按权限域兜底（初审岗办预审与评审、
    # 审核岗办费用评审与财政审核）。填了角色代码就只认这个岗位 ——
    # 立项、招标这类环节实际由谁办，各局不一样，写死在代码里只能二选一
    role = models.CharField('办理角色代码', max_length=32, blank=True, default='')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+',
                                   verbose_name='最后修改人')
    updated = models.DateTimeField('修改时间', auto_now=True)

    class Meta:
        verbose_name = '环节时限'
        verbose_name_plural = '环节时限'
        ordering = ['stage']

    def __str__(self):
        return f'{self.get_stage_display()} {self.days} 天'
