from django.utils import timezone
from rest_framework import serializers

from apps.core.models import User

from . import docgen, flow
from .models import (DocTemplate, PrecheckReport, Project, ProjectDocument,
                     ProjectEvent, ProjectFile, ReviewFile, SupplementNotice)


STAGE_LABELS = dict(Project.Stage.choices)
# 「项目资料」里的文件夹用环节名，但申报时传的可研不叫「申报中材料」，
# 它是这一摞材料的正本，文件夹直接叫「申报材料」
FOLDER_LABELS = {**STAGE_LABELS, 'draft': '申报材料'}


def stage_folder_label(code):
    return FOLDER_LABELS.get(code or '', '未标注环节')


class ProjectFileSerializer(serializers.ModelSerializer):
    doc_type_label = serializers.CharField(source='get_doc_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    size_mb = serializers.SerializerMethodField()
    # 评估报告是审核岗传、申报材料是单位传，同一张表里要能分出谁交的
    uploaded_by_name = serializers.SerializerMethodField()
    stage_label = serializers.SerializerMethodField()
    # 资料清单按办理先后排，要精确到秒：同一环节里「系统出任务书」与
    # 「机构交报告」往往落在同一分钟，只到分钟的格式分不出先后，顺序会反
    created = serializers.DateTimeField(format='iso-8601', read_only=True)

    class Meta:
        model = ProjectFile
        fields = ['id', 'filename', 'doc_type', 'doc_type_label', 'size', 'size_mb',
                  'status', 'status_label', 'error', 'page_count', 'char_count',
                  'round', 'stage', 'stage_label', 'version', 'is_current',
                  'created', 'uploaded_by_name']
        read_only_fields = fields

    def get_size_mb(self, obj):
        return round((obj.size or 0) / 1048576, 2)

    def get_stage_label(self, obj):
        return stage_folder_label(obj.stage)

    def get_uploaded_by_name(self, obj):
        u = obj.uploaded_by
        return (getattr(u, 'display', '') or '') if u else ''


class ProjectListSerializer(serializers.ModelSerializer):
    stage_label = serializers.CharField(read_only=True)
    applicant_display = serializers.SerializerMethodField()
    # 承办 = 具体人（如有）+ 岗位；老数据没有承办人时退回岗位名
    handler_label = serializers.CharField(read_only=True)
    handler_name = serializers.CharField(read_only=True)
    review_result_label = serializers.CharField(read_only=True)
    days_in_stage = serializers.IntegerField(read_only=True)
    file_count = serializers.SerializerMethodField()
    dup_level = serializers.SerializerMethodField()
    dup_attention = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ['id', 'code', 'name', 'applicant', 'unit', 'contact_name', 'contact_phone',
                  'year', 'category', 'investment',
                  'stage', 'stage_label', 'handler_label', 'handler_name', 'applicant_display',
                  'round', 'review_result', 'review_result_label', 'days_in_stage',
                  'file_count', 'dup_level', 'dup_attention', 'sensitive',
                  'submitted_at', 'created', 'updated']

    def get_applicant_display(self, obj):
        return obj.applicant.display if obj.applicant_id else ''

    def get_file_count(self, obj):
        return len(obj.files.all())

    def get_dup_level(self, obj):
        return (obj.dup_check or {}).get('level', '')

    def get_dup_attention(self, obj):
        d = obj.dup_check or {}
        return (d.get('attention') or 0) + (d.get('verify') or 0)


class ProjectWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ['name', 'unit', 'contact_name', 'contact_phone',
                  'year', 'category', 'investment', 'sensitive',
                  'duration_months', 'summary', 'necessity', 'basis']
        extra_kwargs = {
            'investment': {'min_value': 0},
            'duration_months': {'min_value': 1, 'max_value': 120},
        }

    def validate_name(self, v):
        v = (v or '').strip()
        if len(v) < 4:
            raise serializers.ValidationError('项目名称至少 4 个字')
        return v

    def validate_unit(self, v):
        """申报单位只能从市本级名录里选。

        自由输入时同一个单位能写出「市财政局 / 财政局 / 市财政」三种，
        台账按单位归口统计就散了。这里做两件事：能对上名录的换成标准写法，
        对不上的直接拒绝，让前端下拉去选。
        老项目里已存的历史写法允许原样保存（不改动别人的历史记录），
        但新填的值必须能对上。
        """
        from apps.core import org_units

        s = (v or '').strip()
        if not s:
            raise serializers.ValidationError('请选择申报单位')
        std, ok = org_units.normalize(s, allow_inactive=False)
        if ok:
            return std
        old = getattr(self.instance, 'unit', None) if self.instance else None
        if old and s == old:
            return s
        raise serializers.ValidationError(
            f'「{s}」不在市本级申报单位名录内，请从下拉列表中选择；'
            '如果是新设机构，请先让管理员在名录里加上')

    def validate_year(self, v):
        if v is not None and not (2000 <= v <= 2100):
            raise serializers.ValidationError('申报年度不合理')
        return v

    def validate_contact_phone(self, v):
        """只收 11 位手机号。

        原来固话也放行，理由是「受理时联系常用固话」。但这一栏的用途不是
        「能找着人」，是「件到了、被退回、评审出结论时把短信发到他手上」——
        固话收不到短信，填了等于没填，出事还多一层「系统里明明有电话」的
        误会。要留座机就写在联系单位里，这一栏只认手机。
        """
        import re

        s = (v or '').strip().replace(' ', '').replace('-', '')
        if not s:
            return ''
        if re.fullmatch(r'1[3-9]\d{9}', s):
            return s
        raise serializers.ValidationError(
            '请填 11 位手机号：这一栏用来发短信通知，座机收不到；'
            '号码形如 13800000000')


class ProjectEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectEvent
        fields = ['id', 'action', 'action_label', 'from_stage', 'to_stage',
                  'actor_name', 'actor_role', 'actor_role_label', 'comment',
                  'payload', 'created']
        read_only_fields = fields


class ReportSerializer(serializers.ModelSerializer):
    level_label = serializers.CharField(source='get_level_display', read_only=True)
    verdict_label = serializers.CharField(source='get_verdict_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = PrecheckReport
        fields = ['id', 'round', 'level', 'level_label', 'verdict', 'verdict_label',
                  'counts', 'items', 'findings', 'note', 'llm_info',
                  'created_by_name', 'created']
        read_only_fields = fields

    def get_created_by_name(self, obj):
        return obj.created_by.display if obj.created_by_id else ''


class ReviewFileSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source='get_kind_display', read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    # 与材料清单同一口径：排序要精确到秒
    created = serializers.DateTimeField(format='iso-8601', read_only=True)

    class Meta:
        model = ReviewFile
        fields = ['id', 'name', 'kind', 'kind_label', 'size', 'url',
                  'uploaded_by_name', 'created']
        read_only_fields = fields

    def get_uploaded_by_name(self, obj):
        return obj.uploaded_by.display if obj.uploaded_by_id else ''

    def get_url(self, obj):
        return f'/api/projects/{obj.project_id}/review-files/{obj.id}/file/'


class ProjectDocumentSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source='get_kind_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    stage_label = serializers.SerializerMethodField()
    # 与材料清单同一口径：排序要精确到秒
    created = serializers.DateTimeField(format='iso-8601', read_only=True)

    class Meta:
        model = ProjectDocument
        fields = ['id', 'kind', 'kind_label', 'name', 'stage', 'stage_label', 'round',
                  'url', 'meta', 'created_by_name', 'created']
        read_only_fields = fields

    def get_created_by_name(self, obj):
        return obj.created_by.display if obj.created_by_id else ''

    def get_stage_label(self, obj):
        return stage_folder_label(obj.stage)

    def get_url(self, obj):
        return f'/api/projects/{obj.project_id}/documents/{obj.id}/file/'


class DocTemplateSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source='get_kind_display', read_only=True)
    stage_label = serializers.CharField(read_only=True)
    url = serializers.SerializerMethodField()
    # 上传文件必须是可写字段：原来 fields 里没有 file，DRF 直接静默丢弃，
    # 管理员看到的「已保存」只存了文件名，生成的文书仍用打包内置模板。
    file = serializers.FileField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = DocTemplate
        fields = ['id', 'name', 'kind', 'kind_label', 'stage', 'stage_label',
                  'description', 'file', 'filename', 'builtin_key', 'active',
                  'url', 'updated', 'created']
        read_only_fields = ['builtin_key', 'updated', 'created']

    def validate_file(self, value):
        # 只收 docx：docgen 用 python-docx 打开，其它格式会在生成时炸
        name = (value.name or '').lower()
        if not name.endswith('.docx'):
            raise serializers.ValidationError('模板文件必须是 .docx 格式')
        if value.size > 20 * 1024 * 1024:
            raise serializers.ValidationError('模板文件不能超过 20MB')
        return value

    def get_url(self, obj):
        if not obj.file:
            return ''
        return f'/api/templates/{obj.id}/file/'


class NoticeSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    kind_label = serializers.CharField(source='get_kind_display', read_only=True)
    issued_by_name = serializers.SerializerMethodField()

    class Meta:
        model = SupplementNotice
        fields = ['id', 'round', 'kind', 'kind_label', 'items', 'note', 'deadline',
                  'status', 'status_label', 'reply', 'issued_by_name',
                  'issued_at', 'resolved_at']
        read_only_fields = fields

    def get_issued_by_name(self, obj):
        return obj.issued_by.display if obj.issued_by_id else ''


class ProjectDetailSerializer(ProjectListSerializer):
    """详情 = 台账字段 + 材料 + 预审 + 补正 + 评审票数 + 时间轴 + 我能做的事。"""

    files = serializers.SerializerMethodField()
    reports = serializers.SerializerMethodField()
    notices = serializers.SerializerMethodField()
    events = serializers.SerializerMethodField()
    actions = serializers.SerializerMethodField()
    votes = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    applicant_unit = serializers.SerializerMethodField()
    review_scan = serializers.SerializerMethodField()
    review_files = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    precheck_candidates = serializers.SerializerMethodField()
    sla = serializers.SerializerMethodField()
    cost_flow = serializers.SerializerMethodField()
    dup_check = serializers.SerializerMethodField()
    applicant_phone = serializers.SerializerMethodField()
    my_draft = serializers.SerializerMethodField()
    # 批复用数：财评/财政核定优先，没有回落可研投资。前端表单带默认值要用
    budget_wan = serializers.FloatField(read_only=True)

    class Meta(ProjectListSerializer.Meta):
        fields = ProjectListSerializer.Meta.fields + [
            'duration_months', 'summary', 'necessity', 'basis',
            'budget_amount', 'budget_wan',
            'handler_role', 'files', 'reports', 'notices',
            'events', 'actions', 'progress', 'dup_check', 'review_note',
            'votes', 'review_scan', 'review_files', 'concluded_at',
            'audit_result', 'audit_result_label',
            'audit_note', 'audited_at', 'stage_at', 'applicant_unit',
            'applicant_phone',
            'fiscal_result', 'fiscal_result_label', 'fiscal_note', 'fiscal_at',
            'approval_no', 'approval_note', 'approval_at',
            'trial_summary', 'data_collect_scope', 'acceptance_applied_at',
            'documents', 'precheck_candidates', 'sla', 'cost_flow',
            'my_draft',
        ]

    def get_applicant_unit(self, obj):
        return getattr(obj.applicant, 'unit', '') if obj.applicant_id else ''

    def get_applicant_phone(self, obj):
        # 生成对外文书时要填「建设单位联系人电话」，前端拿它做默认值
        return getattr(obj.applicant, 'phone', '') if obj.applicant_id else ''

    def get_files(self, obj):
        return ProjectFileSerializer(obj.files.all(), many=True).data

    def get_reports(self, obj):
        data = ReportSerializer(obj.reports.all()[:20], many=True).data
        user = self.context.get('request').user if self.context.get('request') else None
        # 预审结论（verdict/counts）可以给申报单位看，但 items/findings 是
        # 初审岗的内部工作底稿——系统挑出了哪些毛病、AI 引了哪段原文，
        # 与 get_precheck_candidates 的口径一致，一律不对申报单位下发。
        if user and user.role == User.Role.APPLICANT:
            for row in data:
                row.pop('items', None)
                row.pop('findings', None)
        return data

    def get_notices(self, obj):
        return NoticeSerializer(obj.notices.all()[:10], many=True).data

    def get_review_files(self, obj):
        return ReviewFileSerializer(obj.review_files.all(), many=True).data

    def get_documents(self, obj):
        return ProjectDocumentSerializer(obj.documents.all()[:30], many=True).data

    def get_precheck_candidates(self, obj):
        """预审阶段给初审岗勾选的问题清单。

        其他角色拿到的是空数组：这是初审岗的内部工作清单，
        申报单位不该看到「系统还挑出了哪些毛病」的底稿。
        """
        user = self.context.get('request').user if self.context.get('request') else None
        if not user or user.role not in (User.Role.REVIEWER, User.Role.ADMIN):
            return []
        return docgen.precheck_candidates(obj)

    def get_review_scan(self, obj):
        if not obj.review_scan:
            return None
        return {'name': obj.review_scan_name or obj.review_scan.name.rsplit('/', 1)[-1]}

    def get_events(self, obj):
        return ProjectEventSerializer(obj.events.select_related('actor')[:200],
                                      many=True).data

    def get_votes(self, obj):
        total = (obj.review_agree or 0) + (obj.review_modified or 0) + (obj.review_reject or 0)
        return {'agree': obj.review_agree or 0,
                'modified': obj.review_modified or 0,
                'reject': obj.review_reject or 0,
                'total': total,
                'yes': (obj.review_agree or 0) + (obj.review_modified or 0),
                'recorded': total > 0}

    def get_my_draft(self, obj):
        """我在这一环节存过的草稿：打开办理弹窗时原样带出来。

        只给当前环节的 —— 上一环节存的草稿在件流转时已清掉，这里再兜一层，
        免得阶段变了还把旧内容填给下一个动作。
        """
        user = self.context.get('request').user if self.context.get('request') else None
        if not user or not user.is_authenticated:
            return None
        d = obj.drafts.filter(user=user, stage=obj.stage).first()
        if not d:
            return None
        if d.action_key == flow.HOLD_KEY:
            label = flow.hold_label(d.stage)
        else:
            label = (flow.ACTION_MAP.get(d.action_key) or {}).get('label') or d.action_key
        return {'action_key': d.action_key, 'action_label': label,
                'payload': d.payload,
                'stage': d.stage, 'updated': d.updated.isoformat()}

    def get_actions(self, obj):
        user = self.context.get('request').user if self.context.get('request') else None
        # 详情页要转出选人，动作里带一份候选人清单；待办列表不带，省掉逐件查人
        return flow.available_actions(obj, user, with_assign=True) if user else []

    def get_sla(self, obj):
        return flow.sla_state(obj)

    def get_dup_check(self, obj):
        """重复建设筛查结果：申报单位只看结论，不看别家项目名与原文片段。

        档案库已对业务以外的人关闭，这个字段就是最后一条能摸到历史项目
        正文的路 —— 判重结论该让申报单位知道（他被退的原因），
        但对照清单里的项目名称、功能重合明细、原文片段不下发。
        """
        d = obj.dup_check or {}
        user = self.context.get('request').user if self.context.get('request') else None
        if not user or user.role != User.Role.APPLICANT:
            return d
        keep = ('level', 'level_label', 'summary', 'scanned', 'compared',
                'checked_at', 'version', 'note')
        out = {k: d[k] for k in keep if k in d}
        n = len(d.get('ranking') or [])
        if n:
            out['hidden_note'] = (f'系统比出 {n} 个疑点项目，明细由初审岗核定，'
                                  '此处不下发其他单位材料')
        return out

    def get_cost_flow(self, obj):
        """费用评审轮次：从留痕里还原每一轮退回、补证与结论。

        费用评审允许多轮（单位有异议就退回补证再评），但库里 audit_* 只存
        最后一轮。历史轮次不去另设一张表 —— 每轮的动作本来就写在
        ProjectEvent 里，这里按退回次数编号即可，不再维护两套事实。
        """
        actions = ('cost_review_return', 'cost_resubmit',
                   'cost_review_pass', 'cost_review_reject')
        qs = (obj.events.filter(action__in=actions)
              .select_related('actor').order_by('created', 'id'))
        out = []
        n = 1
        for e in qs:
            payload = e.payload or {}
            row = {
                'id': e.id, 'round': n, 'action': e.action,
                'action_label': e.action_label, 'actor_name': e.actor_name,
                'comment': e.comment, 'items': payload.get('items') or [],
                'created': e.created,
            }
            out.append(row)
            # 一次退回意味着这一轮结束，下一轮从 n+1 开始
            if e.action == 'cost_review_return':
                n += 1
        return out

    def get_progress(self, obj):
        return flow.stage_progress(obj.stage)


class ExpertOptionSerializer(serializers.ModelSerializer):
    display = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'display', 'unit', 'username']
