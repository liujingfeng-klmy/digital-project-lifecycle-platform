"""项目申报、流转与待办的接口。

一条原则：前端不发「我要把状态改成 X」，只发「我要执行 X 动作」。
动作能不能执行、要填什么，由 flow 决定；前端拿 available_actions 渲染，
不重复实现一遍角色判断 —— 两处判断早晚会不一致，出问题时还查不出是谁的错。
"""
import logging
import re
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.files import File
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.localopen import respond_local_open
from apps.core.models import Role, User
from apps.core.permissions import IsAdminRole

from . import docgen, flow, parsing, prefill
from . import diff as diffmod
from . import report as report_mod
from .archiving import ArchiveError, export_project_archive, export_project_package
from .models import (ActionDraft, DocTemplate, Project, ProjectCodeSequence,
                     ProjectDocument, ProjectEvent, ProjectFile, ReviewFile, StageSla,
                     SupplementNotice, TodoAck)
from .serializers import (DocTemplateSerializer, ProjectDetailSerializer,
                          ProjectDocumentSerializer, ProjectFileSerializer,
                          ProjectListSerializer, ProjectWriteSerializer)
from .upload_quota import check_upload_quota

log = logging.getLogger(__name__)

# 在办的阶段（不含储备库与终态）：统计「在办项目数」用
ACTIVE_STAGES = [Project.Stage.PRECHECK, Project.Stage.SUPPLEMENT,
                 Project.Stage.EXPERT_REVIEW, Project.Stage.REVIEW_SUPPLEMENT,
                 Project.Stage.COST_REVIEW, Project.Stage.COST_SUPPLEMENT,
                 Project.Stage.FISCAL_REVIEW,
                 Project.Stage.APPROVAL,
                 Project.Stage.TENDERING, Project.Stage.BUILDING,
                 Project.Stage.TRIAL, Project.Stage.ACCEPTANCE]

# 「今天该我动手」的阶段。储备库是池子不是待办：等资金的项目不该天天挂在
# 审核岗的待办里，需要时从项目台账取
TODO_STAGES = {
    User.Role.APPLICANT: [Project.Stage.DRAFT, Project.Stage.SUPPLEMENT,
                          Project.Stage.REVIEW_SUPPLEMENT,
                          Project.Stage.COST_SUPPLEMENT, Project.Stage.TRIAL],
    # 试运行不再挂初审岗：这一步由建设单位自己跑、自己填，局方只提出具证明
    User.Role.REVIEWER: [Project.Stage.PRECHECK, Project.Stage.EXPERT_REVIEW,
                         Project.Stage.APPROVAL, Project.Stage.TENDERING,
                         Project.Stage.BUILDING],
    User.Role.AUDITOR: [Project.Stage.COST_REVIEW, Project.Stage.FISCAL_REVIEW,
                        Project.Stage.ACCEPTANCE],
}

STAGE_META = [
    {'key': k, 'label': v} for k, v in Project.Stage.choices
]

# 申报单位可自行上传的材料类型白名单（审查项 #1 / #6）。
# 《费用评估报告》由第三方财评机构出具、审核岗代传；《数据归集证明》由我局
# 按模板出具；《专家验收意见》结项后由审核岗备案 —— 这三类绝不能由申报
# 单位用「声明一个 doc_type」的方式混进来顶替。
APPLICANT_DOC_TYPES = frozenset({
    ProjectFile.DocType.PROPOSAL, ProjectFile.DocType.FEASIBILITY,
    ProjectFile.DocType.BUDGET, ProjectFile.DocType.APPROVAL,
    ProjectFile.DocType.EVIDENCE, ProjectFile.DocType.OTHER,
})

# 读材料 / 读文书的 detail action：草稿对这些入口必须不可见（审查项 #3）。
# 写动作（destroy / reassign / flow 等）保持完整 queryset，让无权操作返回
# 明确的 403，而不是用 404 掩盖权限判断。
DRAFT_HIDDEN_ACTIONS = frozenset({
    'list', 'retrieve', 'file_raw', 'file_open', 'file_text', 'file_diff',
    'archive', 'download_document', 'review_file_download', 'review_scan_file',
})

# 台账与统计的「只算自己经办过的」口径（管理员与申报单位另有规则）
SELF_SCOPED_ACTIONS = frozenset({
    'list', 'stats', 'stages', 'report', 'export', 'filters',
})

# 台账读动作：列表、统计、阶段计数、导出。这几个入口回答的都是
# 「已经报出去的件办到哪了」，申报中还没提交的申报单不在这套口径里 ——
# 它躺在申报人手里，只在待办箱/在办箱出现。默认对所有角色排除，
# 别指望每个调用方都记得带 exclude_draft（漏一个就出现「刚建的单跑到台账里」）
LEDGER_ACTIONS = SELF_SCOPED_ACTIONS


def _require_handler(project, user):
    """旁路写操作的承办人校验（审查项 #7）。

    flow.transition 里已有同一条规则，但文书生成、评审材料、查重刷新、
    草稿等入口不走状态机，此前只查角色不查承办人 —— 件改派给 B 之后，
    A 仍能生成并留痕。管理员与申报单位不受此限，handler 为空按岗位池放行。
    """
    if user.role in (User.Role.ADMIN, User.Role.APPLICANT):
        return None
    if project.handler_id and project.handler_id != user.pk:
        return Response(
            {'detail': f'本件当前承办人是「{project.handler_name}」，'
                       '只有承办人本人或管理员可以办理；需要接手请联系管理员改派'},
            status=status.HTTP_403_FORBIDDEN)
    return None


class ProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = Project.objects.select_related('applicant').prefetch_related('files')
        user = self.request.user
        if user.role == User.Role.APPLICANT:
            qs = qs.filter(applicant=user)
        elif user.role != User.Role.ADMIN and self.action in DRAFT_HIDDEN_ACTIONS:
            # 草稿隔离（用户拍板 #22 + 审查项 #3）：未提交的项目只对申报人
            # 和管理员可见。原来只挡 list/retrieve，file_raw / file_text /
            # archive 这些 detail action 的 self.action 不匹配，按原件路径
            # 仍能读到草稿正文 —— 这里按「读动作」统一收窄。
            qs = qs.exclude(stage=Project.Stage.DRAFT)

        # 「谁的项目就只看谁的」：台账与统计只算自己经办过的件。
        # 审核岗原来能在这两页看到别人经办的件（列表右上还有「全部项目」可切），
        # 但这一页叫「已办项目」—— 别人的已办不是我的工作记录。
        # 只收窄读动作：retrieve 之类不动，否则从待办点进一个还没碰过的件会被拒。
        if (user.role not in (User.Role.ADMIN, User.Role.APPLICANT)
                and self.action in SELF_SCOPED_ACTIONS):
            qs = qs.filter(id__in=ProjectEvent.objects
                           .filter(actor=user).values('project_id'))

        p = self.request.query_params
        kw = (p.get('q') or '').strip()
        if kw:
            qs = qs.filter(Q(name__icontains=kw) | Q(code__icontains=kw)
                           | Q(unit__icontains=kw))
        stages = (p.get('stage') or '').strip()
        if stages:
            qs = qs.filter(stage__in=[s for s in stages.split(',') if s])
        # 台账只列「已经报出去」的件：申报中的申报单还在申报人手里，
        # 它在待办箱/在办箱。摆进已办项目会让人以为流程已经启动，
        # 也养成「去台账里找刚建的那张单」的习惯。默认一律排除，
        # include_draft=1 才放行（排查数据用）
        if self.action in LEDGER_ACTIONS and p.get('include_draft') != '1':
            qs = qs.exclude(stage=Project.Stage.DRAFT)
        if p.get('mine') == '1':
            qs = qs.filter(applicant=user)
        # 「我经办的」：这个人在项目上留下过流转动作。初审岗最需要这个 ——
        # 件一通过初审就从待办消失，原来没有任何地方能回看他办过什么。
        #
        # 用子查询取 project_id，不用 events__actor 直接 join：join 之后一个件会
        # 按匹配到的留痕条数变成多行，列表看着没事（有 distinct 兜着），
        # 但 stats / stages 里的 Count('id') 会把这些行一起数进去 ——
        # 库里 1 个储备库件能数出「项目储备库（4）」，投资额也跟着翻倍。
        if p.get('handled') == '1':
            qs = qs.filter(id__in=ProjectEvent.objects
                           .filter(actor=user).values('project_id'))
        if p.get('year'):
            qs = qs.filter(year=p.get('year'))
        unit = (p.get('unit') or '').strip()
        if unit:
            qs = qs.filter(unit=unit)
        cat = (p.get('category') or '').strip()
        if cat:
            qs = qs.filter(category=cat)
        return qs

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ProjectWriteSerializer
        if self.action == 'list':
            return ProjectListSerializer
        return ProjectDetailSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['request'] = self.request
        return ctx

    # ---------------------------------------------------------- 增删改
    def create(self, request, *args, **kwargs):
        if request.user.role != User.Role.APPLICANT:
            return Response({'detail': '只有申报单位账号可以新建申报；'
                                       '其他角色请从台账查看项目'},
                            status=status.HTTP_403_FORBIDDEN)
        s = ProjectWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        now = timezone.now()
        # 发号与建项目必须在同一事务里：否则号码发出去了、项目建失败，
        # 这个号就被一个不存在的项目永久占用
        with transaction.atomic():
            project = s.save(applicant=request.user, code=next_code(),
                             stage=Project.Stage.DRAFT, handler_role=User.Role.APPLICANT,
                             stage_at=now)
            flow._log(project, request.user, 'create', '新建申报', '', Project.Stage.DRAFT,
                      '创建项目', {})

        # 预解析时暂存的原件挂到新项目上。放事务外：解析几十页要几秒，
        # 不该把事务拖长；挂接失败也不回滚项目 —— 表单内容已确认过，
        # 让它留在详情页重传比让用户重填一遍好
        attach = {'attached': False, 'message': ''}
        token = (request.data.get('prefill_token') or '').strip()
        if token:
            attach = _attach_staged(project, token, request.user)
            if attach['attached']:
                flow._log(project, request.user, 'upload', '上传材料', project.stage,
                          project.stage, f"随申报上传《{attach['filename']}》", {})

        data = ProjectDetailSerializer(project, context={'request': request}).data
        data['attach'] = attach
        return Response(data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        project = self.get_object()
        if not _can_edit(project, request.user):
            return Response({'detail': '只有申报单位在「申报中 / 待补正」阶段可以改项目信息；'
                                       '评审后补正阶段只能换材料，不能改投资与名称'},
                            status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """删除项目。

        申报单位只能删自己的草稿；管理员可删任意阶段 —— 台账里的演示数据、
        误建项目总得有人能清。但「能删」不等于「删了就没影」：管理员删除一律
        先把流转留痕与原件归档成 zip（data/deleted/），归档失败则中止删除。
        原来只对申报单位校验归属，初审/审核/管理员拿到的是全量 queryset，
        因此能删掉任何单位的草稿、ProjectEvent 级联消失、留痕断档。
        """
        project = self.get_object()
        user = request.user
        is_admin = user.role == User.Role.ADMIN
        if not is_admin and project.applicant_id != user.pk:
            return Response({'detail': '只有项目所属申报单位或管理员可以删除项目'},
                            status=status.HTTP_403_FORBIDDEN)
        if not is_admin and project.stage != Project.Stage.DRAFT:
            return Response({'detail': '只有「申报中」的项目可以删除；已提交的项目请走撤回或'
                                       '不予立项流程，避免留痕断档'}, status=400)
        # 管理员删除已进入流程的项目，先落一份留痕；草稿没有流转价值，不占磁盘
        archive = None
        if is_admin and project.stage != Project.Stage.DRAFT:
            try:
                archive = export_project_archive(project, user)
            except ArchiveError as exc:
                return Response({'detail': f'留痕归档失败，已中止删除：{exc}'},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        # 原件由 ProjectFile 的 post_delete 信号清理（级联删除同样会触发）
        project.delete()
        if archive:
            return Response({'detail': f'已删除，留痕归档：{archive["name"]}',
                             'archive': archive['name']})
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ---------------------------------------------------------- 流转
    @action(detail=True, methods=['post'], url_path='flow')
    def flow_action(self, request, pk=None):
        project = self.get_object()
        key = (request.data.get('action') or '').strip()
        try:
            project = flow.transition(project, request.user, key, request.data)
        except flow.FlowError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as exc:  # AI 未配置等环境问题
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        project = self.get_queryset().get(pk=project.pk)
        label = flow.ACTION_MAP.get(key, {}).get('label', key)
        return Response({
            'detail': f'已执行：{label}',
            'project': ProjectDetailSerializer(project, context={'request': request}).data,
        })

    @action(detail=True, methods=['post'], url_path='reassign')
    def reassign(self, request, pk=None):
        """管理员改派承办人（用户拍板 #17 A 的配套入口）。

        承办人成为真正办理权后，原承办人停用/转岗必须有收回途径；
        改派只换 handler，不动 stage/stage_at，SLA 停留天数不重置。
        """
        if request.user.role != User.Role.ADMIN:
            return Response({'detail': '仅管理员可以改派'}, status=403)
        project = self.get_object()
        assignee_id = request.data.get('handler_id')
        if not assignee_id:
            # 传空表示收回岗位池：任何该岗位的人都能领
            old = project.handler_name
            project.handler = None
            project.handler_role = flow.handler_role_of(project.stage)
            project.save(update_fields=['handler', 'handler_role', 'updated'])
            flow._log(project, request.user, 'reassign', '收回承办',
                      project.stage, project.stage,
                      f'原承办人：{old or "（无）"}', {'handler_id': None})
            return Response({'detail': '已收回岗位池，该岗位任何人可领取'})
        user = User.objects.filter(pk=assignee_id, is_active=True).first()
        if not user:
            return Response({'detail': '所选账号不存在或已停用'}, status=400)
        expected = flow.handler_role_of(project.stage)
        if expected and user.role != expected:
            return Response({'detail': f'所选账号不属于「{flow.role_name(expected)}」岗位'},
                            status=400)
        old = project.handler_name
        project.handler = user
        project.handler_role = user.role_code
        project.save(update_fields=['handler', 'handler_role', 'updated'])
        flow._log(project, request.user, 'reassign', '改派承办',
                  project.stage, project.stage,
                  f'由「{old or "（无）"}」改派给「{user.display}」',
                  {'handler_id': user.pk, 'handler_name': user.display})
        return Response({'detail': f'已改派给 {user.display}'})

    # ---------------------------------------------------------- 材料
    @action(detail=True, methods=['post'], url_path='files',
            parser_classes=[MultiPartParser, FormParser])
    def upload_file(self, request, pk=None):
        project = self.get_object()
        forced = ''
        if not _can_upload(project, request.user):
            if _can_upload_report(project, request.user):
                forced = ProjectFile.DocType.COST_REPORT   # 只收评估报告，类型由后端定死
            elif _can_upload_proof(project, request.user):
                typed = (request.data.get('doc_type') or '').strip()
                if typed not in PROOF_TYPES:
                    return Response({'detail': '结项后只收《专家验收意见》；'
                                               '《数据归集证明》在待验收时按模板出具'}, status=400)
                forced = typed
            else:
                return Response({'detail': '当前阶段不能增删材料。申报单位只能在「申报中 / 待补正 / '
                                           '评审后补正 / 征求反馈」上传；费用评审环节只有审核岗'
                                           '可上传第三方《费用评估报告》；结项后只有审核岗'
                                           '可补传《专家验收意见》备案'},
                                status=403)
        files = request.FILES.getlist('files') or request.FILES.getlist('file')
        # 配额前置校验：单用户单次 200MB / 10 份，累计 5GB（审查项 #23）
        quota_err = check_upload_quota(request.user, files)
        if quota_err:
            return Response({'detail': quota_err}, status=400)
        doc_type = forced or (request.data.get('doc_type') or '')
        # 申报单位只有这几种类型可声明；第三方的费用评估报告、我局出具的数据
        # 归集证明、结项后的验收意见，都不能靠改一个字段混进来（审查项 #1）。
        if (not forced and request.user.role == User.Role.APPLICANT
                and doc_type and doc_type not in APPLICANT_DOC_TYPES):
            return Response({'detail': '申报单位不能上传《费用评估报告》《数据归集证明》'
                                       '或《专家验收意见》，这类材料由财评机构与我局出具'},
                            status=403)
        results = []
        filled = []
        for f in files:
            try:
                rec = parsing.ingest(project, f, request.user, doc_type)
                got = getattr(rec, 'filled_fields', []) or []
                filled.extend([k for k in got if k not in filled])
                results.append({
                    'filename': rec.filename, 'ok': rec.status == ProjectFile.Status.READY,
                    'message': (f'解析完成，{rec.char_count} 字 / {rec.page_count} 页'
                                if rec.status == ProjectFile.Status.READY else rec.error),
                    'filled': [{'key': k, 'label': parsing.BACKFILL_FIELDS.get(k, k)}
                               for k in got],
                    'file': ProjectFileSerializer(rec).data,
                })
            except parsing.UploadError as exc:
                results.append({'filename': getattr(f, 'name', ''), 'ok': False,
                                'message': str(exc), 'filled': []})
        flow._log(project, request.user, 'upload', '上传材料', project.stage, project.stage,
                  f'上传 {len(files)} 份材料',
                  {'ok': sum(1 for r in results if r['ok'])})
        project.refresh_from_db()
        # 回填后仍缺的必填项，直接告诉用户去补，别等他点提交才被拦
        missing = [{'key': k, 'label': prefill.FIELD_LABEL[k], 'hint': prefill.FIELD_HINT[k]}
                   for k in prefill.REQUIRED_FIELDS
                   if getattr(project, k) in (None, '')]
        return Response({
            'results': results,
            'filled': [{'key': k, 'label': parsing.BACKFILL_FIELDS.get(k, k)} for k in filled],
            'missing_required': missing,
            'project': ProjectDetailSerializer(project, context={'request': request}).data,
        })

    @action(detail=True, methods=['delete'], url_path=r'files/(?P<file_id>\d+)')
    def delete_file(self, request, pk=None, file_id=None):
        project = self.get_object()
        rec = get_object_or_404(ProjectFile, pk=file_id, project=project)
        # 删除按「类型白名单 + 上传人」双校验（审查项 #4）：
        # 申报单位只能删自己在允许阶段传的申报类材料，不能删审核岗传来的
        # 费用评估报告 —— 那玩意一删，费用评审就没有判断依据了。
        can_delete = False
        if _can_upload(project, request.user):
            if request.user.role == User.Role.APPLICANT:
                can_delete = (rec.uploaded_by_id == request.user.pk
                              and rec.doc_type in APPLICANT_DOC_TYPES)
            else:
                can_delete = True
        elif (_can_upload_report(project, request.user)
              and rec.doc_type == ProjectFile.DocType.COST_REPORT):
            can_delete = True
        elif (_can_upload_proof(project, request.user)
              and rec.doc_type in PROOF_TYPES):
            can_delete = True
        if not can_delete:
            return Response({'detail': '当前阶段不能删除这份材料：只能删除本人上传的申报材料，'
                                       '财评机构或我局出具的材料请联系管理员'}, status=403)
        name = rec.filename
        # 原件由 post_delete 信号统一清理，这里不再重复删
        rec.delete()
        flow._log(project, request.user, 'remove_file', '删除材料', project.stage,
                  project.stage, f'删除《{name}》', {})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'], url_path=r'files/(?P<file_id>\d+)/raw')
    def file_raw(self, request, pk=None, file_id=None):
        """材料原件流。前端拿 blob 打开或下载，不暴露 MEDIA_URL 直链。

        get_object() 已经按角色收窄过 queryset：申报单位只能取到自己名下的项目，
        所以这里不必再判一次归属，但仍要挡住「原件文件已丢失」的情况 —— 库里
        有记录、盘上没文件，直接返回空流会让浏览器存出 0 字节文件。
        """
        project = self.get_object()
        rec = get_object_or_404(ProjectFile, pk=file_id, project=project)
        if not rec.file:
            return Response({'detail': '该材料没有原件'}, status=404)
        try:
            handle = rec.file.open('rb')
        except FileNotFoundError:
            return Response({'detail': '原件已丢失，可能被移动或手工删除过'},
                            status=404)
        return FileResponse(handle, as_attachment=False, filename=rec.filename)

    @action(detail=True, methods=['post'], url_path=r'files/(?P<file_id>\d+)/open')
    def file_open(self, request, pk=None, file_id=None):
        """用本机默认程序打开材料原件（仅限本机访问）。

        浏览器渲染不了 docx/xlsx，前端只能下载；但本机跑服务时可以让后端把文件
        落到下载目录并调起 Word，省掉「下载→去文件夹里找→双击」三步。
        """
        project = self.get_object()
        rec = get_object_or_404(ProjectFile, pk=file_id, project=project)
        return respond_local_open(request, rec.file, rec.filename)

    @action(detail=True, methods=['get'], url_path=r'files/(?P<file_id>\d+)/text')
    def file_text(self, request, pk=None, file_id=None):
        project = self.get_object()
        rec = get_object_or_404(ProjectFile, pk=file_id, project=project)
        return Response({'id': rec.id, 'filename': rec.filename,
                         'text': rec.text, 'char_count': rec.char_count,
                         'sections': [{'title': s.get('title', ''),
                                       'category_label': s.get('category_label', ''),
                                       'chars': len(s.get('text') or '')}
                                      for s in (rec.sections or [])]})

    @action(detail=True, methods=['get'], url_path=r'files/(?P<file_id>\d+)/diff')
    def file_diff(self, request, pk=None, file_id=None):
        """补正前后对照：这份材料相对上一版改了什么。

        默认比对同类型的上一版；也可以传 against=<file_id> 指定比哪一版。
        """
        project = self.get_object()
        cur = get_object_or_404(ProjectFile, pk=file_id, project=project)
        against = request.query_params.get('against')
        if against:
            prev = get_object_or_404(ProjectFile, pk=against, project=project)
        else:
            prev = diffmod.select_pair(project.files, cur)
        if not prev:
            return Response({'detail': '这是第一版材料，没有上一版可比', 'available': False},
                            status=400)
        if not prev.text or not cur.text:
            return Response({'detail': '有一版材料没有解析出正文（可能是扫描件），无法比对'},
                            status=400)
        result = diffmod.compare_texts(prev.text, cur.text)
        result.update({
            'available': True,
            'summary': diffmod.summarize(result),
            'from': {'id': prev.id, 'filename': prev.filename, 'version': prev.version,
                     'round': prev.round, 'created': prev.created},
            'to': {'id': cur.id, 'filename': cur.filename, 'version': cur.version,
                   'round': cur.round, 'created': cur.created},
        })
        return Response(result)

    @action(detail=True, methods=['patch'], url_path=r'files/(?P<file_id>\d+)/type')
    def file_type(self, request, pk=None, file_id=None):
        project = self.get_object()
        if not _can_upload(project, request.user):
            return Response({'detail': '当前阶段不能修改材料类型'}, status=403)
        rec = get_object_or_404(ProjectFile, pk=file_id, project=project)
        # 改类型同样受白名单约束（审查项 #6）：申报单位不得把已有可研改成
        # 《费用评估报告》，也不得改掉审核岗上传材料的类型。
        if request.user.role == User.Role.APPLICANT and (
                rec.uploaded_by_id != request.user.pk
                or rec.doc_type not in APPLICANT_DOC_TYPES):
            return Response({'detail': '只能修改本人上传的申报材料类型'}, status=403)
        new_type = request.data.get('doc_type') or ''
        if (request.user.role == User.Role.APPLICANT
                and new_type and new_type not in APPLICANT_DOC_TYPES):
            return Response({'detail': '不能把材料改成《费用评估报告》等类型'}, status=403)
        try:
            parsing.move_file(rec, new_type)
        except parsing.UploadError as exc:
            return Response({'detail': str(exc)}, status=400)
        return Response(ProjectFileSerializer(rec).data)

    # ---------------------------------------------------------- 预解析
    @action(detail=False, methods=['post'], url_path='prefill',
            parser_classes=[MultiPartParser, FormParser])
    def prefill_material(self, request):
        """上传可研/建设方案，先解析出字段，不建项目。

        申报弹窗里「先传材料再填表」靠这个接口：返回能识别的字段、识别不出的
        字段（含去模板哪一节补），以及可用于建项目的暂存 token。
        """
        if request.user.role not in (User.Role.APPLICANT, User.Role.ADMIN):
            return Response({'detail': '只有申报单位可以上传申报材料'}, status=403)
        uploaded = request.FILES.get('file')
        quota_err = check_upload_quota(request.user, [uploaded] if uploaded else [])
        if quota_err:
            return Response({'detail': quota_err}, status=400)
        name = Path(uploaded.name or 'material').name
        ext = Path(name).suffix.lower()
        if ext in getattr(parsing.textparse, 'SHEET_EXT', set()):
            return Response({'detail': '这里解析的是可研/建设方案正文，不收 Excel。'
                                       '投资预算表请在项目建好后到「申报材料」里上传'}, status=400)
        if ext in getattr(parsing.textparse, 'SHEET_LEGACY', set()):
            return Response({'detail': f'暂不支持 {ext} 旧版表格，请用 Excel 另存为 '
                                       '.xlsx 后重传'}, status=400)
        if ext in getattr(parsing.textparse, 'LEGACY', set()):
            return Response({'detail': f'暂不支持 {ext} 格式，请在 Word 里另存为 '
                                       '.docx 后重传'}, status=400)
        if ext not in getattr(parsing.textparse, 'SUPPORTED', set()):
            return Response({'detail': f'不支持的文件类型 {ext}，支持 PDF / DOCX / TXT'},
                            status=400)
        if uploaded.size > parsing.MAX_BYTES:
            return Response({'detail': f'文件 {uploaded.size / 1048576:.0f}MB 超过 200MB 上限'},
                            status=400)

        token, path, filename = prefill.stage_file(uploaded)
        # 申报人对本份材料勾选的涉敏标记：预解析在建项目前发生，靠它决定是否
        # 允许把章节文本发给配置的模型服务
        sensitive = str(request.data.get('sensitive', '')).lower() in ('1', 'true', 'yes')
        try:
            data = prefill.analyze(path, filename, sensitive=sensitive)
        except Exception as exc:
            prefill.drop_stage(token)
            log.info('预解析失败 %s：%s', filename, exc)
            return Response({'detail': f'《{filename}》解析失败：{exc}'}, status=400)

        data['token'] = token
        data['doc_type'] = parsing.guess_doc_type(filename)
        data['doc_type_label'] = dict(ProjectFile.DocType.choices).get(data['doc_type'], '')
        return Response(data)

    @action(detail=False, methods=['get'], url_path='feasibility-template')
    def feasibility_template(self, request):
        """可研报告模板下载。解析不全时前端直接引导用户按模板改材料。"""
        path = Path(settings.BASE_DIR) / 'apps' / 'project' / 'docs' / 'feasibility_template.docx'
        if not path.exists():
            return Response({'detail': '模板文件缺失，请联系管理员'}, status=404)
        return FileResponse(open(path, 'rb'), as_attachment=True,
                            filename='可行性研究报告模板.docx')

    # ---------------------------------------------------------- 评审会材料
    REVIEW_FILE_EXT = {'.pdf', '.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff'}
    # 结论录完也可能发现漏了一张专家表，所以进到费用评审还允许补；
    # 再往后材料就归档了，不该再动
    REVIEW_FILE_STAGES = (Project.Stage.EXPERT_REVIEW,
                          Project.Stage.REVIEW_SUPPLEMENT,
                          Project.Stage.COST_REVIEW)

    @action(detail=True, methods=['post'], url_path='review-files',
            parser_classes=[MultiPartParser, FormParser])
    def upload_review_files(self, request, pk=None):
        """上传评审会材料：专家意见表、汇总意见表，可一次多份。

        现实里一场评审会收上来的是七八张纸，以前只能拼成一个 PDF，
        或者只传汇总表。判定依据不全，事后追责全凭记忆 —— 所以按份存。
        """
        project = self.get_object()
        err = _require_handler(project, request.user)
        if err:
            return err
        if request.user.role not in (User.Role.REVIEWER, User.Role.ADMIN):
            return Response({'detail': '只有初审岗或管理员可以上传评审会材料'},
                            status=403)
        if project.stage not in self.REVIEW_FILE_STAGES:
            return Response({'detail': '项目还没进评审，或材料已归档，不能再上传评审会材料'},
                            status=400)

        kind = request.data.get('kind') or ReviewFile.Kind.EXPERT
        if kind not in dict(ReviewFile.Kind.choices):
            return Response({'detail': '材料类型不正确'}, status=400)

        files = request.FILES.getlist('files') or request.FILES.getlist('file')
        quota_err = check_upload_quota(request.user, files)
        if quota_err:
            return Response({'detail': quota_err}, status=400)

        saved, skipped = [], []
        for f in files:
            name = Path(f.name or 'scan').name
            ext = Path(name).suffix.lower()
            if ext not in self.REVIEW_FILE_EXT:
                skipped.append(f'{name}：只支持 PDF 与图片')
                continue
            if f.size > 30 * 1024 * 1024:
                skipped.append(f'{name}：超过 30MB，请压缩或拆分')
                continue
            rec = ReviewFile.objects.create(
                project=project, file=f, name=name, kind=kind, size=f.size,
                uploaded_by=request.user)
            saved.append(rec)

        if not saved:
            return Response({'detail': '；'.join(skipped) or '没有可用的文件'},
                            status=400)

        label = dict(ReviewFile.Kind.choices)[kind]
        names = '、'.join(r.name for r in saved)
        flow._log(project, request.user, 'review_files',
                  f'上传{label}', project.stage, project.stage,
                  f'上传{label} {len(saved)} 份：{names}', {})
        detail = ProjectDetailSerializer(project, context={'request': request}).data
        msg = f'已上传 {len(saved)} 份{label}'
        if skipped:
            msg += f'；{len(skipped)} 份未通过：' + '；'.join(skipped)
        return Response({'detail': msg, 'saved': len(saved),
                         'skipped': skipped, 'project': detail})

    @action(detail=True, methods=['delete'],
            url_path=r'review-files/(?P<file_id>\d+)')
    def delete_review_file(self, request, pk=None, file_id=None):
        project = self.get_object()
        err = _require_handler(project, request.user)
        if err:
            return err
        if request.user.role not in (User.Role.REVIEWER, User.Role.ADMIN):
            return Response({'detail': '只有初审岗或管理员可以删除评审会材料'},
                            status=403)
        if project.stage not in self.REVIEW_FILE_STAGES:
            return Response({'detail': '材料已归档，不能再删除'}, status=400)
        rec = project.review_files.filter(pk=file_id).first()
        if not rec:
            return Response({'detail': '文件不存在'}, status=404)
        name = rec.name
        rec.file.delete(save=False)
        rec.delete()
        flow._log(project, request.user, 'review_files', '删除评审会材料',
                  project.stage, project.stage, f'删除《{name}》', {})
        return Response({'detail': f'已删除《{name}》'})

    @action(detail=True, methods=['get'],
            url_path=r'review-files/(?P<file_id>\d+)/file')
    def review_file_download(self, request, pk=None, file_id=None):
        project = self.get_object()
        rec = project.review_files.filter(pk=file_id).first()
        if not rec:
            return Response({'detail': '文件不存在'}, status=404)
        try:
            handle = rec.file.open('rb')
        except FileNotFoundError:
            return Response({'detail': '原件已丢失，请重新上传'}, status=404)
        return FileResponse(handle, as_attachment=False, filename=rec.name)

    @action(detail=True, methods=['get'], url_path='review-scan/file')
    def review_scan_file(self, request, pk=None):
        """老项目只有单份 review_scan 时的兼容入口。"""
        project = self.get_object()
        if not project.review_scan:
            return Response({'detail': '还没有上传评审会材料'}, status=404)
        try:
            handle = project.review_scan.open('rb')
        except FileNotFoundError:
            return Response({'detail': '原件已丢失，请重新上传'}, status=404)
        return FileResponse(handle, as_attachment=False,
                            filename=project.review_scan_name or '评审会材料')

    # ---------------------------------------------------------- 生成文书
    def _doc_response(self, request, project, rec, detail):
        flow._log(project, request.user, 'document', '生成文书',
                  project.stage, project.stage, detail, {'doc_id': rec.id})
        return Response({
            'detail': detail,
            'doc': ProjectDocumentSerializer(rec).data,
            'project': ProjectDetailSerializer(project,
                                               context={'request': request}).data,
        })

    @action(detail=True, methods=['post'], url_path='docs/precheck-notice')
    def gen_precheck_notice(self, request, pk=None):
        """按勾选的问题生成《预审修正说明》。

        只认 key，不认前端传来的标题 —— 候选清单由后端按本轮报告重算，
        前端能改的只有「选哪几条」，改不了条目内容和严重程度。
        """
        project = self.get_object()
        err = _require_handler(project, request.user)
        if err:
            return err
        if request.user.role not in (User.Role.REVIEWER, User.Role.ADMIN):
            return Response({'detail': '只有初审岗可以生成预审修正说明'}, status=403)
        if project.stage != Project.Stage.PRECHECK:
            return Response({'detail': '只有预审中的项目可以生成预审修正说明'},
                            status=400)
        keys = request.data.get('keys') or []
        if not isinstance(keys, list) or not keys:
            return Response({'detail': '请至少勾选一条要写入说明的问题'}, status=400)
        items = docgen.pick_candidates(project, keys)
        if not items:
            return Response({'detail': '勾选的问题已失效（预审报告可能重跑过），'
                                       '请刷新页面重新勾选'}, status=400)
        deadline = parse_date(str(request.data.get('deadline') or ''))
        if request.data.get('deadline') and not deadline:
            return Response({'detail': '期限格式不正确，应为 2026-11-27'}, status=400)
        rec = docgen.generate_precheck_notice(project, items, deadline, request.user)
        return self._doc_response(request, project, rec,
                                  f'已生成《预审修正说明》，含 {len(items)} 条问题')

    @action(detail=True, methods=['post'], url_path='docs/cost-review-task')
    def gen_cost_review_task(self, request, pk=None):
        """生成给财评机构的《费用评审任务书》，评审期限默认 7 个自然日。"""
        project = self.get_object()
        err = _require_handler(project, request.user)
        if err:
            return err
        if request.user.role not in (User.Role.AUDITOR, User.Role.ADMIN):
            return Response({'detail': '只有审核岗可以生成费用评审任务书'}, status=403)
        if project.stage != Project.Stage.COST_REVIEW:
            return Response({'detail': '只有费用评审阶段可以生成任务书'}, status=400)
        try:
            days = int(request.data.get('days') or 7)
        except (TypeError, ValueError):
            return Response({'detail': '评审期限必须是整数天'}, status=400)
        if not (1 <= days <= 90):
            return Response({'detail': '评审期限应在 1—90 天之间'}, status=400)
        # 电话填了就必须是手机 11 位或带区号的固话；没填则回退经办人/申报人档案值，
        # 回退值同样要过校验 —— 档案里手填的 10 位号照样会印到对外文书上。
        applicant = project.applicant
        phones = {}
        for key, label, fallback in (
            ('entrust_phone', '委托单位联系电话', getattr(request.user, 'phone', '')),
            ('unit_phone', '建设单位联系电话',
             getattr(applicant, 'phone', '') if applicant else ''),
        ):
            raw = str(request.data.get(key) or '').strip()
            val = _norm_phone(raw or fallback)
            if val is None:
                return Response({'detail': f'{label}格式不正确：{PHONE_HINT}'},
                                status=400)
            phones[key] = val
        data = {
            'entrust_unit': (request.data.get('entrust_unit') or '').strip(),
            # 委托单位联系人＝我局经办人；建设单位联系人＝申报单位经办人。
            # 两者都是任务书受理对接口，别混成「机构联系人」一个框。
            'entrust_contact': (request.data.get('entrust_contact') or '').strip(),
            'entrust_phone': phones['entrust_phone'],
            'unit_contact': (request.data.get('unit_contact') or '').strip(),
            'unit_phone': phones['unit_phone'],
            'doc_no': (request.data.get('doc_no') or '').strip(),
            'days': days,
        }
        if not data['entrust_unit']:
            return Response({'detail': '请填写受托的财评机构名称'}, status=400)
        rec = docgen.generate_cost_task(project, data, request.user)
        return self._doc_response(request, project, rec,
                                  f'已生成《费用评审任务书》，评审期限 {days} 个自然日')

    # ---------------------------------------------------------- 办理草稿
    @action(detail=True, methods=['get', 'post', 'delete'], url_path='draft')
    def draft(self, request, pk=None):
        """存 / 取 / 丢我在这一环节的办理草稿。

        存过草稿的件就算「在办」：工作台上从待办移进在办箱，回头能接着填。
        草稿只是半成品，不算一次流转 —— 不进留痕、不改阶段、不影响别人的待办。
        """
        project = self.get_object()
        if request.method == 'GET':
            d = ActionDraft.objects.filter(project=project, user=request.user,
                                           stage=project.stage).first()
            return Response(_draft_data(d) if d else None)

        if request.method == 'DELETE':
            n, _ = ActionDraft.objects.filter(project=project, user=request.user,
                                              stage=project.stage).delete()
            return Response({'detail': '已丢弃草稿' if n else '这一环节没有你的草稿'})

        if request.method == 'POST':
            err = _require_handler(project, request.user)
            if err:
                return err
        key = (request.data.get('action_key') or '').strip()
        payload = request.data.get('payload') or {}
        if not isinstance(payload, dict):
            return Response({'detail': '草稿内容格式不对'}, status=400)
        actions = flow.available_actions(project, request.user)
        if not actions:
            return Response({'detail': '这一环节没有可办的动作，草稿存不了。'
                                       '刷新页面看最新环节。'}, status=400)
        if key == flow.HOLD_KEY:
            # 「暂存」不是流转动作：件还在本环节、还在我手上，只是从待办
            # 挪进在办箱。费用评审要等财评机构几天出报告，没有这一步，
            # 件要么一直挂在待办里催，要么被逼着提前转走。
            block = flow.hold_block_reason(project)
            if block:
                return Response({'detail': block}, status=400)
            allowed = flow.HOLD_FIELDS
        else:
            spec = next((a for a in actions if a['key'] == key), None)
            if spec is None:
                return Response({'detail': '这个动作现在不该你办，草稿存不了。'
                                           '刷新页面看最新环节。'}, status=400)
            # 只留这个动作真正会提交的字段：前端状态里的临时键、别的动作的
            # 遗留值都别带进去，否则回填时会冒出莫名其妙的空行
            allowed = {f['key'] for f in spec.get('form') or []} | {'assignee'}
        clean = {k: v for k, v in payload.items() if k in allowed}
        d, _created = ActionDraft.objects.update_or_create(
            project=project, user=request.user, stage=project.stage,
            defaults={'action_key': key, 'payload': clean})
        return Response({'detail': '已暂存到在办箱，报告回来接着办'
                         if key == flow.HOLD_KEY else '已存入在办箱，随时回来接着填',
                         'draft': _draft_data(d)})

    @action(detail=True, methods=['post'], url_path='docs/data-collect')
    def gen_data_collect(self, request, pk=None):
        """出具《数据归集证明》。

        待验收的硬门槛：数据没归集就不能验收。证明由我局按模板出具、
        发给建设单位，不是让单位传一份扫描件来凑 —— 那样谁都能补一张图。
        """
        project = self.get_object()
        err = _require_handler(project, request.user)
        if err:
            return err
        if request.user.role not in (User.Role.AUDITOR, User.Role.ADMIN):
            return Response({'detail': '只有审核岗可以出具数据归集证明'}, status=403)
        if project.stage != Project.Stage.ACCEPTANCE:
            return Response({'detail': '只有待验收阶段可以出具数据归集证明'}, status=400)
        trial = (request.data.get('trial_summary') or '').strip()
        scope = (request.data.get('data_collect_scope') or '').strip()
        # 审核岗修正后的表述与申请验收时同口径校验（审查项 #16）：
        # 1 个字的表述不能被盖章认下来，更不能覆盖申报人的原始说明。
        if trial and len(trial) < 15:
            return Response({'detail': '试运行情况至少 15 个字'}, status=400)
        if scope and len(scope) < 10:
            return Response({'detail': '数据归集情况至少 10 个字'}, status=400)
        changed = []
        if trial and trial != project.trial_summary:
            changed.append('试运行情况')
        if scope and scope != project.data_collect_scope:
            changed.append('数据归集情况')
        if changed:
            flow._log(project, request.user, 'proof_scope_fix', '修正归集口径',
                      project.stage, project.stage,
                      f'出具证明前修正：{"、".join(changed)}', {})
        try:
            rec = docgen.generate_data_collect(project, {
                'doc_no': (request.data.get('doc_no') or '').strip(),
                'trial_summary': trial,
                'data_collect_scope': scope,
            }, request.user)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)
        # 出具时补改过的表述回写到项目上：证明与项目记录必须是同一份口径，
        # 否则日后调档会看到两个版本，说不清哪份盖了章
        fields = []
        if trial and trial != project.trial_summary:
            project.trial_summary = trial
            fields.append('trial_summary')
        if scope and scope != project.data_collect_scope:
            project.data_collect_scope = scope
            fields.append('data_collect_scope')
        if fields:
            project.save(update_fields=[*fields, 'updated'])
        return self._doc_response(request, project, rec,
                                  '已出具《数据归集证明》，下载盖章后送建设单位')

    @action(detail=True, methods=['post'], url_path='docs/approval-reply')
    def gen_approval_reply(self, request, pk=None):
        """生成给申报单位的《立项批复》。"""
        project = self.get_object()
        err = _require_handler(project, request.user)
        if err:
            return err
        if request.user.role not in (User.Role.REVIEWER, User.Role.ADMIN):
            return Response({'detail': '只有初审岗可以生成立项批复'}, status=403)
        if project.stage != Project.Stage.APPROVAL:
            return Response({'detail': '只有立项阶段可以生成立项批复'}, status=400)
        rec = docgen.generate_approval(project, {
            'approval_no': (request.data.get('approval_no') or '').strip(),
        }, request.user)
        return self._doc_response(request, project, rec, '已生成《立项批复》')

    @action(detail=True, methods=['get'],
            url_path=r'documents/(?P<doc_id>\d+)/file')
    def download_document(self, request, pk=None, doc_id=None):
        project = self.get_object()
        rec = project.documents.filter(pk=doc_id).first()
        if not rec:
            return Response({'detail': '文书不存在'}, status=404)
        try:
            handle = rec.file.open('rb')
        except FileNotFoundError:
            return Response({'detail': '文书原件已丢失，请重新生成'}, status=404)
        return FileResponse(handle, as_attachment=True, filename=rec.name)

    # ---------------------------------------------------------- 结项归档
    @action(detail=True, methods=['get'], url_path='archive')
    def archive_package(self, request, pk=None):
        """一个项目一份完整资料：材料（含各轮历史版本）+ 生成文书 + 评审会材料 + 过程留痕。

        结项后单位要存档、报审计、移交档案室，靠翻系统不现实，得能一次拿走。
        包落 data/exports/，同项目保留最近 3 份。
        """
        project = self.get_object()
        # 服务端硬门槛：前端 canExport 只认 accepted，后端必须同样拦住。
        # 半套材料既不能当档案也不能当凭证，管理员也不能绕过。
        if project.stage != Project.Stage.ACCEPTED:
            return Response(
                {'detail': '只有已验收项目才能导出归档包；在办件是半套材料，不予导出。'},
                status=403)
        try:
            info = export_project_package(project, request.user)
        except ArchiveError as exc:
            return Response({'detail': f'归档包生成失败：{exc}'}, status=400)
        return FileResponse(open(info['path'], 'rb'), as_attachment=True,
                            filename=info['name'])

    # ---------------------------------------------------------- 待办已读
    @action(detail=True, methods=['post'], url_path='todo-ack')
    def todo_ack(self, request, pk=None):
        """进过详情就把侧栏待办数减一。

        按「人 + 项目 + 阶段 + 轮次」记：项目换阶段或开新一轮时旧标记失效，
        新工作项重新计数。重复调用是幂等的。
        """
        project = self.get_object()
        user = request.user
        if project.stage not in _stages_for(user):
            return Response({'detail': '该项目当前不是你的待办'}, status=400)
        if user.role == User.Role.APPLICANT and project.applicant_id != user.pk:
            return Response({'detail': '只能查看本单位申报的项目'}, status=403)
        # 申报中的单子不记已读：它还没交出去，点开填材料不算「已经动手」，
        # 记了就会从待办箱消失，人以为新建的单丢了
        if project.stage == Project.Stage.DRAFT:
            return Response({'pending': _pending_count(user)})
        try:
            TodoAck.objects.get_or_create(
                user=user, project=project, stage=project.stage, round=project.round)
        except IntegrityError:
            # 两个标签页同时打开同一个项目时可能同时 insert，唯一约束挡下即可
            pass
        return Response({'pending': _pending_count(user)})

    # ---------------------------------------------------------- 重复建设对照
    @action(detail=True, methods=['post'], url_path='dup-refresh')
    def dup_refresh(self, request, pk=None):
        """重跑一次重复建设比对，并把逐项对照存回项目。

        提交申报与规则预审都会自动算一遍，但那时可能还没有历史档案，
        或者项目已经过了预审、申报单位又补传了材料。初审要逐项对照时
        直接点「重新比对」，不必为了刷新查重去跑一整轮规则预审。
        """
        project = self.get_object()
        err = _require_handler(project, request.user)
        if err:
            return err
        if request.user.role not in (User.Role.REVIEWER, User.Role.AUDITOR,
                                     User.Role.ADMIN):
            return Response({'detail': '只有初审岗、审核岗可以重新比对'},
                            status=status.HTTP_403_FORBIDDEN)
        if not project.files.filter(status='ready').exists():
            return Response({'detail': '该项目还没有解析成功的材料，无法比对'}, status=400)
        from . import precheck
        dup = precheck.screen_duplicate(project)
        project.dup_check = dup
        project.save(update_fields=['dup_check', 'updated'])
        return Response({'dup_check': dup})

    # ---------------------------------------------------------- 统计与选项
    @action(detail=False, methods=['get'])
    def stats(self, request):
        qs = self.get_queryset()
        by_stage = {row['stage']: row['n'] for row in
                    qs.values('stage').annotate(n=Count('id'))}
        total_inv = qs.aggregate(s=Sum('investment'))['s'] or 0
        # 用 values_list 取单列：qs 带了 select_related('applicant')，
        # 再 .only('dup_check') 会报「字段既被延迟又走 select_related」。
        flagged = sum(1 for d in qs.values_list('dup_check', flat=True)
                      if (d or {}).get('attention'))
        return Response({
            'total': qs.count(),
            'active': qs.filter(stage__in=ACTIVE_STAGES).count(),
            'by_stage': [{'key': m['key'], 'label': m['label'],
                          'count': by_stage.get(m['key'], 0)} for m in STAGE_META],
            'investment_total': float(total_inv),
            'dup_flagged': flagged,
        })

    @action(detail=False, methods=['get'])
    def stages(self, request):
        # 角标必须和列表同口径：原来用全库 Project.objects 统计，
        # 申报单位看到「预审中 5 个」点进去只有自己的 1 个
        qs = self.get_queryset()
        counts = {row['stage']: row['n'] for row in
                  qs.values('stage').annotate(n=Count('id'))}
        return Response({
            'stages': [{'key': m['key'], 'label': m['label'],
                        'count': counts.get(m['key'], 0)} for m in STAGE_META],
            'roles': [{'key': r.code, 'label': r.name, 'scope': r.scope}
                      for r in Role.objects.filter(active=True)],
        })

    # ---------------------------------------------------------- 统计与导出
    @action(detail=False, methods=['get'])
    def report(self, request):
        return Response(report_mod.build(self.get_queryset()))

    @action(detail=False, methods=['get'])
    def filters(self, request):
        """统计页筛选下拉：只列库里真出现过的单位与领域，不列全量字典。"""
        qs = self.get_queryset()
        units = list(qs.exclude(unit='').values_list('unit', flat=True)
                     .distinct().order_by('unit'))
        cats = list(qs.exclude(category='').values_list('category', flat=True)
                    .distinct().order_by('category'))
        years = list(qs.exclude(year=None).values_list('year', flat=True)
                     .distinct().order_by('-year'))
        return Response({'units': units, 'categories': cats, 'years': years})

    @action(detail=False, methods=['get'])
    def export(self, request):
        kind = request.query_params.get('kind') or 'projects'
        if kind not in ('projects', 'events'):
            return Response({'detail': '不支持的导出类型'}, status=400)
        head, rows = report_mod.csv_response_rows(self.get_queryset(), kind)
        body = report_mod.to_csv(head, rows)
        name = ('项目流转明细' if kind == 'events' else '项目台账')
        stamp = timezone.localtime().strftime('%Y%m%d-%H%M')
        resp = HttpResponse(body, content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = (
            f"attachment; filename*=UTF-8''{quote(f'{name}-{stamp}.csv')}")
        return resp


class StageSlaView(APIView):
    """各环节审查时限：全员可读，只有管理员能改。

    读接口给全员是有意的：办理人要能看见「这一步还剩几天」，
    否则时限只存在于管理员的设置页里，对实际办件没有约束力。
    """

    def get_permissions(self):
        if self.request.method in ('PUT', 'PATCH'):
            return [IsAdminRole()]
        return [IsAuthenticated()]

    def get(self, request):
        rows = {r.stage: r for r in StageSla.objects.all()}
        # 环节可选的岗位：只能挑同一权限域下的角色。跨域指过去，这个环节的
        # 动作按钮在那个岗位手里根本不出现（动作按权限域授权），等于把件
        # 转进死胡同。域内换岗位则是正常的：立项交给「立项审批岗」办
        roles = list(Role.objects.filter(active=True)
                     .values('code', 'name', 'scope').order_by('order', 'id'))
        out = []
        for stage in flow.SLA_STAGES:
            rec = rows.get(stage)
            scope = flow.STAGE_ROLE.get(stage, '')
            out.append({
                'stage': stage,
                'label': Project.Stage(stage).label,
                'days': rec.days if rec and rec.days else flow.SLA_DEFAULTS[stage],
                'default': flow.SLA_DEFAULTS[stage],
                'role': (rec.role if rec else '') or '',
                'role_label': flow.role_name((rec.role if rec else '') or ''),
                'scope': scope,
                'scope_label': flow.ROLE_LABEL.get(scope, ''),
                'role_options': [r for r in roles if r['scope'] == scope],
                'updated_by': rec.updated_by.display if rec and rec.updated_by_id else '',
                'updated': rec.updated if rec else None,
                # 已办项目数，用来判断这个环节改时限会影响到多少在办件
                'active': Project.objects.filter(stage=stage).count(),
            })
        return Response({'items': out})

    def put(self, request):
        items = request.data.get('items')
        if not isinstance(items, list) or not items:
            return Response({'detail': '请提交要调整的环节设置'}, status=400)
        valid = {s for s in flow.SLA_STAGES}
        changed = []
        for it in items:
            stage = (it.get('stage') or '').strip()
            if stage not in valid:
                return Response({'detail': f'未知环节：{stage}'}, status=400)
            try:
                days = int(it.get('days'))
            except (TypeError, ValueError):
                return Response({'detail': f'「{it.get("label") or stage}」的时限必须是整数'},
                                status=400)
            if days < 0 or days > 365:
                return Response({'detail': '时限应在 0—365 天之间；填 0 表示不计时'},
                                status=400)
            role_code = (it.get('role') or '').strip()
            defaults = {'days': days, 'updated_by': request.user}
            if role_code:
                role = Role.objects.filter(code=role_code, active=True).first()
                if role is None:
                    return Response({'detail': f'岗位不存在或已停用：{role_code}'},
                                    status=400)
                if role.scope != flow.STAGE_ROLE.get(stage, ''):
                    return Response(
                        {'detail': f'「{Project.Stage(stage).label}」按权限域归属'
                                   f'{flow.ROLE_LABEL.get(flow.STAGE_ROLE.get(stage, ""), "")}，'
                                   f'不能指给「{role.name}」（{flow.ROLE_LABEL.get(role.scope, role.scope)}）'},
                        status=400)
                defaults['role'] = role_code
            else:
                # 空 = 回到按权限域兜底
                defaults['role'] = ''
            rec, _ = StageSla.objects.update_or_create(stage=stage, defaults=defaults)
            changed.append({'stage': stage, 'days': rec.days, 'role': rec.role})
        return Response({'detail': f'已更新 {len(changed)} 个环节的设置',
                         'items': changed})


# 联系电话：手机 11 位，或带区号的固定电话（区号 010/02x 三位、其余四位，
# 号码 7—8 位，可分机）。只卡位数与形态，不校验号段真实性 ——
# 号段表年年变，硬编码会把新号段挡在外面。
PHONE_RE = re.compile(
    r'^(?:1[3-9]\d{9}|(?:010|02\d|0\d{3})-?\d{7,8}(?:-\d{1,6})?)$')
PHONE_HINT = '手机填 11 位；固定电话带区号，如 0990-6220000'


def _norm_phone(value):
    """去空格、统一连字符。空值返回 ''，格式不合法返回 None。"""
    s = str(value or '').strip()
    s = s.replace(' ', '').replace('　', '').replace('－', '-').replace('—', '-')
    if not s:
        return ''
    return s if PHONE_RE.match(s) else None


def next_code():
    """按年度发号：读独立序列，不取「当前最大号 +1」。

    取最大号的老问题：删掉 XM2026-009 再新建，又会拿到 009，
    两个不同项目在导出表、审计记录里撞号。序列只增不回退，
    号码永久占用，删项目也不会被复用。
    """
    year = timezone.now().year
    prefix = f'XM{year}-'
    for _ in range(3):
        try:
            with transaction.atomic():
                seq, _created = (ProjectCodeSequence.objects
                                 .select_for_update()
                                 .get_or_create(year=year))
                # 历史数据可能已经发到 009 而序列为空/落后，跳过去
                for _ in range(1000):
                    seq.last_no += 1
                    code = f'{prefix}{seq.last_no:03d}'
                    if not Project.objects.filter(code=code).exists():
                        seq.save(update_fields=['last_no'])
                        return code
                raise RuntimeError('年度编号已用尽，请检查数据')
        except IntegrityError:
            # 两个请求同时首次建该年度序列，唯一约束挡下一个，重试即可
            continue
    raise RuntimeError('项目编号生成失败，请重试')


def _can_edit(project, user):
    if user.role == User.Role.ADMIN:
        return project.stage == Project.Stage.DRAFT
    if user.role != User.Role.APPLICANT or project.applicant_id != user.pk:
        return False
    return project.stage in (Project.Stage.DRAFT, Project.Stage.SUPPLEMENT)


def _can_upload_report(project, user):
    """费用评审环节，审核岗能不能传第三方《费用评估报告》。

    评估报告是财评机构出的，不是申报材料，所以不走「申报材料」那套权限：
    申报单位在评审阶段本就该冻结材料，只放开审核岗这一类人、只放开这一种类型，
    避免借着传报告顺手把可研、投资概算也换掉。
    """
    return (project.stage == Project.Stage.COST_REVIEW
            and user.role in (User.Role.AUDITOR, User.Role.ADMIN))


# 结项后唯一还要收的一份：专家验收意见（盖章签字的那张，报我局备案）。
# 《数据归集证明》不再是上传件 —— 它按模板出具，存的是生成文书不是扫描件
PROOF_TYPES = (ProjectFile.DocType.ACCEPT_OPINION,)


def _can_upload_proof(project, user):
    """已验收（结项后），审核岗能不能上传专家验收意见。

    验收会开完才有这份签字件，所以它只能在结项之后收；只放开这一类人、
    只放开这一种类型，不让「上传材料」变成把申报正本换掉的后门。
    """
    return (project.stage == Project.Stage.ACCEPTED
            and user.role in (User.Role.AUDITOR, User.Role.ADMIN))


def _can_upload(project, user):
    """能不能增删材料。

    比 `_can_edit` 多两个阶段：「评审后补正」和「征求反馈」。
    这两步就是让申报单位换材料的，但不能顺手把项目名称、投资也改掉 ——
    投资一变，评审会的结论就作废了。
    """
    return (_can_edit(project, user)
            or (user.role == User.Role.APPLICANT
                and project.applicant_id == user.pk
                and project.stage in (Project.Stage.REVIEW_SUPPLEMENT,
                                      Project.Stage.COST_SUPPLEMENT)))


def _attach_staged(project, token, user):
    """把预解析暂存的原件挂到新建的项目上。"""
    path, filename = prefill.load_stage(token)
    if not path:
        return {'attached': False, 'filename': '',
                'message': '预解析的材料已过期（超过 24 小时），请在详情页重新上传'}
    try:
        handle = open(path, 'rb')
    except OSError:
        return {'attached': False, 'filename': filename, 'message': '暂存的原件已丢失'}
    try:
        rec = parsing.ingest(project, File(handle, name=filename), user, '')
    finally:
        handle.close()
    prefill.drop_stage(token)
    if rec.status == ProjectFile.Status.READY:
        return {'attached': True, 'filename': filename,
                'message': f'已解析 {rec.char_count} 字 / {rec.page_count} 页'}
    return {'attached': False, 'filename': filename,
            'message': rec.error or '材料已上传但解析未成功，请在详情页查看'}


class TodoView(APIView):
    """按角色出待办。不是权限清单，是「今天该我动手的项目」。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        ctx = {'request': request}
        items = []

        qs = (_todo_qs(user)
              .prefetch_related('files', 'notices', 'reports'))
        # 点开过的件已经进了在办箱，待办箱只留「还没动手」的：
        # 两个箱子各管一段，同一件在两边都挂着，人会以为有两件要办
        doing = _doing_ids(user)
        if doing:
            qs = qs.exclude(id__in=doing)
        for p in qs:
            if user.role == User.Role.APPLICANT:
                notice = next((n for n in p.notices.all()
                               if n.status == SupplementNotice.Status.OPEN), None)
                items.append(_todo(p, ctx, notice=notice))
            else:
                items.append(_todo(p, ctx))

        items.sort(key=lambda x: (-x['days_in_stage'], x['id']))
        # count 是「该我办的全部件数」；pending 是「还没点开过的件数」。
        # 侧栏角标用 pending —— 点进去看完、还没办，角标先减一，别一直催
        return Response({'count': len(items), 'pending': _pending_count(user),
                         'items': items})


def _scope_for_user(qs, user):
    """待办只看「该我办的」。

    申报单位看自己名下的；局内岗位看转出时点给自己的那一件；没指定承办人
    的老件（以及岗位没人的情形）仍按岗位下发，否则升级之后所有在办件
    都会从待办里消失。
    """
    if user.role == User.Role.APPLICANT:
        return qs.filter(applicant=user)
    return qs.filter(Q(handler=user) | Q(handler__isnull=True))


def _stages_for(user):
    """我的待办阶段。

    除了按权限域取，还要扣掉「指定了别的岗位」的环节：管理员把立项指给
    「立项审批岗」之后，初审岗就不该在待办里看到立项件 —— 看见了也办不了，
    只会天天点进去确认自己不能办。
    """
    stages = list(TODO_STAGES.get(user.role, []))
    if not stages or user.role == User.Role.ADMIN:
        return stages
    overrides = {s.stage: s.role for s in StageSla.objects
                 .exclude(role='').filter(stage__in=stages)}
    if not overrides:
        return stages
    code = getattr(getattr(user, 'role_ref', None), 'code', '') or ''
    return [s for s in stages if not overrides.get(s) or overrides[s] == code]


def _todo_qs(user):
    stages = _stages_for(user)
    if not stages:
        return Project.objects.none()
    return _scope_for_user(Project.objects.filter(stage__in=stages), user)


def _ack_q(user):
    """当前阶段的有效「已读」标记。

    申报中的单子例外：它还没交出去，点开看一眼不是「已经动手」。
    新建申报会直接落详情页填材料，若照常记已读，件立刻从待办箱挪到在办箱，
    申报人回工作台找不到自己刚建的单 —— 那才是「新建的申报去哪了」的由来。
    """
    return TodoAck.objects.filter(
        user=user, project=OuterRef('pk'), stage=OuterRef('stage'),
        round=OuterRef('round'),
    ).exclude(stage=Project.Stage.DRAFT)


def _pending_count(user):
    """未点开的待办数：待办全集减去已读标记，再减去自己存过草稿的件。

    存了草稿的件已经移到「在办箱」，侧栏角标不该还在催它 ——
    角标是「有几件没动手」，不是「有几件没办完」。
    """
    ack = _ack_q(user)
    drafted = ActionDraft.objects.filter(user=user, project=OuterRef('pk'),
                                         stage=OuterRef('stage'))
    return (_todo_qs(user).annotate(_acked=Exists(ack), _drafted=Exists(drafted))
            .filter(_acked=False, _drafted=False).count())


def _draft_map(user):
    """自己在当前环节存过的草稿，按项目 id 索引。

    当前环节的草稿才算数：上一环节的草稿在流转时已删，这里再兜一层，
    免得老数据里的残留把件挂在两个箱子里。
    """
    return {d.project_id: d for d in ActionDraft.objects
            .filter(user=user).select_related('project')
            if d.stage == d.project.stage}


def _doing_ids(user):
    """在办箱里的件：点开过的、存过草稿的。

    口径（2026-11-24）：待办箱里点进去就算在办 —— 点开就是要动手，不是
    看一眼。点开即从待办移到在办箱，办完提交自动移出。原来只有存草稿
    才进在办箱，结果「点开了、正在办」的件在两个箱子里都找不到。

    申报中的申报单不进在办箱：它不是「办到一半的审批件」，而是「还没交的
    申报单」，从头到尾都该在待办箱里等着提交。它的已读标记本身也不生效
    （见 _ack_q），这里再兜一层，免得老数据里的残留把它挪走。
    """
    ids = set(_draft_map(user))
    ack = _ack_q(user)
    ids |= set(_todo_qs(user)
               .exclude(stage=Project.Stage.DRAFT)
               .annotate(_acked=Exists(ack))
               .filter(_acked=True).values_list('id', flat=True))
    return ids


def _draft_data(d):
    """草稿对外的样子：动作、内容、保存时间。"""
    if d.action_key == flow.HOLD_KEY:
        # 暂存不是真动作，ACTION_MAP 里查不到，别把 'hold' 这个内部键
        # 摆到在办箱的行上去掉价
        label = flow.hold_label(d.stage)
    else:
        label = (flow.ACTION_MAP.get(d.action_key) or {}).get('label') or d.action_key
    return {'action_key': d.action_key, 'action_label': label,
            'payload': d.payload, 'stage': d.stage,
            'updated': d.updated.isoformat()}


def _todo(project, ctx, notice=None):
    actions = flow.available_actions(project, ctx['request'].user)
    action_label = actions[0]['label'] if actions else '等待其他角色办理'
    data = {
        'id': project.id, 'code': project.code, 'name': project.name,
        'unit': project.unit, 'stage': project.stage,
        'stage_label': project.stage_label,
        # 承办人：转出时点给谁就显示谁，没点人时显示岗位
        'handler_label': project.handler_label,
        'days_in_stage': project.days_in_stage, 'round': project.round,
        'investment': project.investment_wan,
        'actions': [{'key': a['key'], 'label': a['label']} for a in actions],
        'action_label': action_label,
        # 待办行上直接给「看材料」入口：办评审会要先读可研，
        # 从列表再点进详情、往下翻到第二屏才找到材料，太绕
        'files': sum(1 for f in project.files.all() if f.status == 'ready'),
    }
    if notice is not None:
        data['notice'] = {'round': notice.round, 'items': notice.items,
                          'deadline': notice.deadline, 'note': notice.note}
    return data


class InboxView(APIView):
    """我的两个箱子：待办箱 + 在办箱。

    两个箱子都只算自己 —— 别人手上、全局有多少在办，与「今天该我干什么」
    无关，那些数在「统计分析」里看。原来工作台顶部显示全局在办，
    办理人看到的是别人的工作量，除了添乱没有用处。

    待办箱：该我动手、还没点开过的件；
    在办箱：我点开过、还没提交的件（含点过保存的），以及申报单位还没提交
            预审的申报单。
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        ctx = {'request': request}

        drafts = _draft_map(user)
        doing_ids = _doing_ids(user)

        doing = []
        if doing_ids:
            qs = (Project.objects.filter(id__in=doing_ids)
                  .prefetch_related('files', 'notices'))
            for p in qs:
                notice = None
                if user.role == User.Role.APPLICANT:
                    notice = next((n for n in p.notices.all()
                                   if n.status == SupplementNotice.Status.OPEN), None)
                item = _todo(p, ctx, notice=notice)
                d = drafts.get(p.id)
                item['draft'] = _draft_data(d) if d else None
                doing.append(item)
        # 最近保存的排前面：手上有事，回来接着填的就是最后动过那一件
        doing.sort(key=lambda x: (x.get('draft') or {}).get('updated') or '', reverse=True)

        stages = _stages_for(user)
        todos = []
        if stages:
            qs = _scope_for_user(
                Project.objects.filter(stage__in=stages)
                .prefetch_related('files', 'notices', 'reports'), user)
            if doing_ids:
                qs = qs.exclude(id__in=doing_ids)
            for p in qs:
                if user.role == User.Role.APPLICANT:
                    notice = next((n for n in p.notices.all()
                                   if n.status == SupplementNotice.Status.OPEN), None)
                    todos.append(_todo(p, ctx, notice=notice))
                else:
                    todos.append(_todo(p, ctx))
        todos.sort(key=lambda x: (-x['days_in_stage'], x['id']))

        return Response({
            'todos': {'count': len(todos), 'items': todos},
            'doing': {'count': len(doing), 'items': doing},
            'pending': _pending_count(user),
        })


class PendingCountView(APIView):
    """侧栏与首页用的待办数，比整份待办轻。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'pending': _pending_count(request.user),
                         'doing': len(_doing_ids(request.user))})


class DocTemplateViewSet(viewsets.ModelViewSet):
    """模板库：仅管理员可见、可维护。

    管理员在这里上传/替换模板，并选择挂到哪个环节。文书类型（kind）决定
    系统在哪个动作里调用它；环节（stage）用于归类展示与筛选。
    """

    queryset = DocTemplate.objects.all()
    serializer_class = DocTemplateSerializer
    permission_classes = [IsAdminRole]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _filename(self):
        f = self.request.FILES.get('file')
        return f.name if f else ''

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user, filename=self._filename())

    def perform_update(self, serializer):
        name = self._filename()
        if name:
            serializer.save(uploaded_by=self.request.user, filename=name)
        else:
            serializer.save(uploaded_by=self.request.user)

    @action(detail=True, methods=['get'], url_path='file')
    def download(self, request, pk=None):
        obj = self.get_object()
        if obj.file:
            try:
                return FileResponse(obj.file.open('rb'), as_attachment=True,
                                    filename=obj.filename or obj.name)
            except FileNotFoundError:
                return Response({'detail': '模板文件已丢失，请重新上传'}, status=404)
        # 内置模板没有上传件：把后端打包的那份给管理员下载，便于改完再传回
        key = obj.builtin_key or obj.kind
        path = docgen.PACKAGED / f'{key}.docx'
        if not path.exists():
            return Response({'detail': '内置模板不存在'}, status=404)
        return FileResponse(open(path, 'rb'), as_attachment=True,
                            filename=f'{obj.name}.docx')

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.file:
            obj.file.delete(save=False)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
