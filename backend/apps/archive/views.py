import hashlib
import logging
import os
import tempfile
from pathlib import Path

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import FileResponse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.localopen import respond_local_open
from apps.core.models import User
from apps.project.upload_quota import check_upload_quota

from . import extract
from . import functions as funcmod
from . import services, similarity, textparse
from .models import ArchiveFile, ArchiveProject, CompareReview
from .serializers import (ArchiveFileBriefSerializer, ArchiveFileDetailSerializer,
                          ArchiveProjectSerializer, ArchiveProjectWriteSerializer,
                          CompareReviewSerializer, CompareReviewWriteSerializer)

log = logging.getLogger(__name__)


class CanMaintainArchive(IsAuthenticated):
    """历史档案的写权限：申报单位只读，其余业务角色与管理员可维护。

    申报单位能比对自己的材料有没有和历史项目撞车，但不能改档案
    —— 档案是判重依据，让申报单位能改就等于让他自己裁量。
    """

    message = '仅初审岗、审核岗与管理员可以维护历史档案'

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return request.user.role in (User.Role.REVIEWER, User.Role.AUDITOR, User.Role.ADMIN)


class CanAdminArchive(IsAuthenticated):
    """档案的破坏性操作只给管理员。

    原来删除、重解析、合并档案归初审/审核岗，但列表、原文、详情只给管理员
    —— 出现"业务岗看不到档案，却能按自增 ID 删掉审计档案"的权限倒挂。
    这类操作一旦误用无法撤销，统一收敛到管理员。
    """

    message = '仅管理员可执行该档案操作'

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return request.user.role == User.Role.ADMIN


class CanReviewCompare(IsAuthenticated):
    """谁能核定重复建设疑点。

    申报单位不能 —— 让被审的一方给自己下结论，等于没有核定。
    评审会结论由初审岗录入，故初审岗是主责人，审核岗与管理员同样可核定。
    """

    message = '仅初审岗、审核岗与管理员可以核定重复建设疑点'

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return request.user.role in (User.Role.REVIEWER, User.Role.AUDITOR,
                                     User.Role.ADMIN)


class CanReadArchiveDetail(IsAuthenticated):
    """历史档案的查阅权限：仅管理员。

    2026-11-21 收紧过一次。原来的口径是「申报单位不能读、业务角色都能读」，
    结果任何业务账号都能把历年别家单位的可研翻个遍 —— 判重只需要系统算出
    的疑点与片段（/archive/compare/），不需要把整个档案库list出来。
    现在列表、检索、原文、统计一律只给管理员；初审岗要归档仍然能导入，
    但导完看不到别人的档案，避免有人借判重之名摸底。
    """

    message = '历史档案仅管理员可查阅；需要核对历史项目请联系管理员'

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return request.user.role == User.Role.ADMIN


class ArchiveProjectViewSet(viewsets.ModelViewSet):
    queryset = ArchiveProject.objects.all().prefetch_related('files')
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ArchiveProjectWriteSerializer
        return ArchiveProjectSerializer

    def get_permissions(self):
        # 破坏性/批量一致性操作收敛到管理员：能看到档案的人才动得了档案，
        # 消除"业务岗按 ID 就能删审计档案却看不到内容"的倒挂。
        if self.action in ('destroy', 'merge', 'rebuild', 'year_apply'):
            return [CanAdminArchive()]
        # 归档录入与信息维护仍归业务岗（这是他们的职责）
        if self.action in ('create', 'update', 'partial_update',
                           'import_files', 'year_diff'):
            return [CanMaintainArchive()]
        # 读：列表、检索、单项目详情 —— 只给管理员
        return [CanReadArchiveDetail()]

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        kw = (p.get('q') or '').strip()
        if kw:
            qs = qs.filter(Q(name__icontains=kw) | Q(unit__icontains=kw) |
                           Q(category__icontains=kw) | Q(note__icontains=kw) |
                           Q(files__filename__icontains=kw)).distinct()
        if p.get('year'):
            qs = qs.filter(year=p['year'])
        if p.get('status'):
            qs = qs.filter(status=p['status'])
        if p.get('category'):
            qs = qs.filter(category=p['category'])
        if p.get('has_file') == '1':
            qs = qs.annotate(n=Count('files')).filter(n__gt=0)
        return qs.order_by('-year', 'name')

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        old_year = obj.year
        resp = super().update(request, *args, **kwargs)
        obj.refresh_from_db()
        # 人改过的年度打上 manual，之后「年度核对」不会再把它列出来
        if obj.year != old_year:
            obj.year_source = 'manual'
            obj.save(update_fields=['year_source'])
        return resp

    # ------------------------------------------------------------ 年度核对
    @staticmethod
    def _best_meta(project):
        """取这个项目里第一份解析就绪的材料，重算一次元数据。

        档案项目可能挂多份材料（建议书、可研、批复），以最早入库的那份为准：
        后补的批复里也可能出现年份，但不代表立项年度。
        """
        rec = (project.files.filter(status=ArchiveFile.Status.READY)
               .order_by('id').first())
        if not rec or not rec.text:
            return None
        return extract.extract(rec.text, rec.filename)

    @action(detail=False, methods=['get'], url_path='year-diff')
    def year_diff(self, request):
        """列出「库里年度 ≠ 新算法识别年度」的项目，交给管理员勾选应用。

        不自动覆盖是有意的：库里 2025 的四个项目，材料是 2023 年编制、
        2025 年才立项，按材料年份改反而改错。清单摆出来，人对一眼再决定。
        """
        items = []
        for p in ArchiveProject.objects.prefetch_related('files').order_by('id'):
            meta = self._best_meta(p)
            if not meta or not meta.get('year') or meta['year'] == p.year:
                continue
            items.append({
                'id': p.id, 'name': p.name, 'unit': p.unit,
                'old_year': p.year, 'new_year': meta['year'],
                'year_source': meta.get('year_source') or '',
                'file_year_source': {
                    'label': '封面申报日期', 'filename': '文件名',
                    'frequency': '正文高频年份', 'hint': '导入时指定',
                }.get(meta.get('year_source'), '未识别'),
            })
        return Response({'count': len(items), 'items': items})

    @action(detail=False, methods=['post'], url_path='year-apply')
    def year_apply(self, request):
        ids = request.data.get('ids') or []
        if not isinstance(ids, list) or not ids:
            return Response({'detail': '请先勾选要应用的项目'}, status=400)
        changed, skipped = [], []
        for p in ArchiveProject.objects.filter(pk__in=ids).prefetch_related('files'):
            meta = self._best_meta(p)
            if not meta or not meta.get('year'):
                skipped.append({'id': p.id, 'name': p.name, 'reason': '材料没有可识别的年度'})
                continue
            if meta['year'] == p.year:
                continue
            changed.append({'id': p.id, 'name': p.name, 'from': p.year,
                            'to': meta['year']})
            p.year = meta['year']
            p.year_source = meta.get('year_source') or ''
            p.save(update_fields=['year', 'year_source', 'updated'])
        return Response({'changed': len(changed), 'items': changed, 'skipped': skipped})

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        # 有重复建设核定的项目不能删：核定台账「只增不改、审计可追溯」，
        # 删项目会连带删掉审计要查的东西。原件由 post_delete 信号清理
        if obj.reviews.exists():
            return Response(
                {'detail': '该项目已有重复建设核定记录，不能删除。'
                           '如需停止使用，请把运行状态改为「已停用」'},
                status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():
                resp = super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response({'detail': '该项目存在关联的核定记录，不能删除'},
                            status=status.HTTP_400_BAD_REQUEST)
        return resp

    # ------------------------------------------------------------ 导入

    @action(methods=['post'], detail=False, url_path='import',
            parser_classes=[MultiPartParser, FormParser])
    def import_files(self, request):
        files = request.FILES.getlist('files') or request.FILES.getlist('file')
        # 与项目材料同一套配额（#23）：单次 200MB / 10 份、累计 5GB
        quota_err = check_upload_quota(request.user, files)
        if quota_err:
            return Response({'detail': quota_err}, status=400)

        project = None
        pid = request.data.get('project')
        if pid:
            project = ArchiveProject.objects.filter(pk=pid).first()

        year = request.data.get('year')
        year = int(year) if str(year or '').isdigit() else None
        doc_type = request.data.get('doc_type') or None

        results = []
        for f in files:
            try:
                r = services.ingest_upload(f, request.user, project=project,
                                           doc_type=doc_type, year=year)
            except Exception as exc:  # 单个文件炸了不能影响其余
                log.exception('导入失败 %s', getattr(f, 'name', '?'))
                r = {'ok': False, 'skipped': False, 'filename': getattr(f, 'name', '?'),
                     'message': f'导入异常：{exc}'}
            results.append(r)

        ok = sum(1 for r in results if r.get('ok') and not r.get('skipped'))
        skipped = sum(1 for r in results if r.get('skipped'))
        failed = sum(1 for r in results if not r.get('ok'))
        return Response({
            'results': results,
            'summary': {'total': len(results), 'ok': ok, 'skipped': skipped, 'failed': failed},
        })

    # ------------------------------------------------------------ 合并

    @action(methods=['post'], detail=False)
    def merge(self, request):
        ids = request.data.get('source_ids') or []
        target_id = request.data.get('target_id')
        if not ids or not target_id:
            return Response({'detail': '需要 source_ids 与 target_id'}, status=400)
        target = ArchiveProject.objects.filter(pk=target_id).first()
        if not target:
            return Response({'detail': '目标项目不存在'}, status=404)
        # 源项目若已有核定记录，合并会连带删掉核定台账（现在是 PROTECT，
        # 直接删会抛异常），先在这里说清楚卡在哪
        blocked = list(CompareReview.objects
                       .filter(project_id__in=[i for i in ids if str(i) != str(target_id)])
                       .values_list('project__name', flat=True).distinct())
        if blocked:
            return Response(
                {'detail': '以下项目已有重复建设核定记录，不能合并：'
                           + '、'.join(blocked[:5])},
                status=status.HTTP_400_BAD_REQUEST)
        moved = ArchiveFile.objects.filter(project_id__in=ids).update(project=target)
        ArchiveProject.objects.filter(pk__in=ids).exclude(pk=target_id).delete()
        return Response({'moved_files': moved, 'target': ArchiveProjectSerializer(target).data})

    # ------------------------------------------------------------ 重算指纹

    @action(methods=['post'], detail=False)
    def rebuild(self, request):
        """只重算章节、词频与功能项，不重新解析原件 —— 算法升级后用。"""
        n = 0
        for f in ArchiveFile.objects.filter(status=ArchiveFile.Status.READY).iterator():
            if not f.text:
                continue
            sections = textparse.split_sections(f.text)
            for s in sections:
                s['chars'] = len(s['text'])
            f.sections = sections
            f.grams = similarity.build_grams(f.text)
            f.functions = funcmod.extract_functions(sections)
            f.save(update_fields=['sections', 'grams', 'functions'])
            n += 1
        return Response({'rebuilt': n})


class ArchiveFileViewSet(viewsets.GenericViewSet):
    queryset = ArchiveFile.objects.select_related('project').all()
    serializer_class = ArchiveFileDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ('destroy', 'reparse', 'move'):
            return [CanAdminArchive()]
        if self.action in ('retrieve', 'text', 'raw', 'open_local'):
            return [CanReadArchiveDetail()]
        return [IsAuthenticated()]

    def retrieve(self, request, pk=None):
        obj = self.get_object()
        return Response(self.get_serializer(obj).data)

    @action(methods=['get'], detail=True, url_path='raw')
    def raw(self, request, pk=None):
        """原件流，供页面内直接打开或下载。

        不走 MEDIA_URL 直链：直链一旦被转发，就绕过了 CanReadArchiveDetail，
        申报单位也能翻别家单位的历史可研。这里每次请求都过一遍角色校验。
        """
        obj = self.get_object()
        if not obj.file:
            return Response({'detail': '该材料没有原件'}, status=404)
        try:
            handle = obj.file.open('rb')
        except FileNotFoundError:
            return Response({'detail': '原件已丢失，可能被移动或手工删除过'},
                            status=404)
        return FileResponse(handle, as_attachment=False, filename=obj.filename)

    @action(methods=['post'], detail=True, url_path='open')
    def open_local(self, request, pk=None):
        """用本机默认程序打开原件（仅限本机访问）。

        方法名不能叫 open（会遮蔽内置函数），路由仍是 /raw 旁边的 /open/。
        """
        obj = self.get_object()
        return respond_local_open(request, obj.file, obj.filename)

    def destroy(self, request, pk=None):
        obj = self.get_object()
        # 原件由 post_delete 信号清理
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(methods=['get'], detail=True)
    def text(self, request, pk=None):
        obj = self.get_object()
        return Response({'id': obj.id, 'filename': obj.filename,
                         'char_count': obj.char_count, 'text': obj.text})

    @action(methods=['post'], detail=True)
    def reparse(self, request, pk=None):
        obj = self.get_object()
        obj, meta = services.parse_into_record(obj)
        if meta is None:
            return Response({'detail': obj.error}, status=400)
        if meta.get('name') and obj.project.name in ('未命名材料', '（待归入）'):
            obj.project.name = meta['name']
            obj.project.save(update_fields=['name', 'updated'])
        return Response(ArchiveFileDetailSerializer(obj).data)

    @action(methods=['post'], detail=True)
    def move(self, request, pk=None):
        obj = self.get_object()
        target_id = request.data.get('project')
        target = ArchiveProject.objects.filter(pk=target_id).first()
        if not target:
            return Response({'detail': '目标项目不存在'}, status=404)
        old = obj.project
        obj.project = target
        obj.save(update_fields=['project'])
        if old and old.name in ('（待归入）',) and not old.files.exists():
            old.delete()
        return Response({'ok': True, 'project_id': target.id, 'project': target.name})


class CompareView(APIView):
    """把一份材料与历史档案比对。

    输入三种来路：
      file      直接上传一份（不落库，适合"先试一下"）
      file_id   已在库里的档案材料（用于互查）
      text      粘贴正文

    2026-11-21 起只给业务角色（初审/审核/管理员）：档案库整体收起之后，
    这个接口是唯一还能取到片段的地方，不能再对申报单位开放 ——
    否则「不能查历史档案」只是界面上的限制，换个人拿正文来比就绕过去了。
    """

    permission_classes = [CanReviewCompare]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        payload = request.data
        file_obj = request.FILES.get('file')
        file_id = payload.get('file_id')
        tmp_path = None
        digest_hex = ''

        if file_obj:
            ext = Path(file_obj.name or '').suffix.lower() or '.txt'
            if ext not in textparse.SUPPORTED:
                return Response({'detail': f'不支持的文件类型 {ext}'}, status=400)
            # 比对接口原来不设上限：导入有 200MB 限制，这里却能塞任意大文件，
            # 而且解析是同步占请求线程的
            if (file_obj.size or 0) > services.MAX_BYTES:
                return Response(
                    {'detail': f'文件 {file_obj.size / 1048576:.0f}MB 超过 '
                               f'{services.MAX_BYTES // 1048576}MB 上限'},
                    status=400)
            fd, tmp_path = tempfile.mkstemp(suffix=ext)
            digest = hashlib.sha256()
            try:
                with os.fdopen(fd, 'wb') as fh:
                    for chunk in file_obj.chunks():
                        digest.update(chunk)
                        fh.write(chunk)
                raw, _ = textparse.parse_file(Path(tmp_path))
                text = textparse.clean_text(raw)
                filename = file_obj.name
                digest_hex = digest.hexdigest()
            except Exception as exc:
                return Response({'detail': f'解析失败：{exc}'}, status=400)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
        elif file_id:
            doc = ArchiveFile.objects.filter(pk=file_id).first()
            if not doc or not doc.text:
                return Response({'detail': '该材料没有可用的解析文本'}, status=400)
            text, filename = doc.text, doc.filename
        else:
            text = (payload.get('text') or '').strip()
            filename = payload.get('filename') or ''

        if len(similarity.normalize(text)) < 100:
            return Response({'detail': '正文太短（不足 100 字），无法做有意义的比对'}, status=400)

        q = services.build_query({
            'text': text,
            'name': payload.get('name') or '',
            'unit': payload.get('unit') or '',
            'year': payload.get('year') or None,
            'investment': payload.get('investment') or None,
            'filename': filename,
        })

        def _int(key, default):
            try:
                return int(payload.get(key) or default)
            except (TypeError, ValueError):
                return default

        scope = payload.get('scope_project_ids')
        if isinstance(scope, str):
            scope = [int(x) for x in scope.split(',') if x.strip().isdigit()] or None

        # 同一份正文在库里的记录要排除，否则会跟自己撞出 100%。
        # 按正文指纹查，不按文件指纹 —— 另存为另一格式后文件指纹就变了。
        cond = Q(text_hash=similarity.text_fingerprint(text))
        if digest_hex:
            cond |= Q(sha256=digest_hex)
        self_docs = list(ArchiveFile.objects.filter(cond).select_related('project'))
        if file_id:
            self_docs += list(ArchiveFile.objects.filter(pk=file_id).select_related('project'))
        exclude_ids = list({d.id for d in self_docs})

        result = services.run_compare(
            q,
            scope_project_ids=scope,
            top_n=_int('top_n', 8),
            snippet_top=_int('snippet_top', 4),
            include_snippets=str(payload.get('snippets', '1')) != '0',
            exclude_file_ids=exclude_ids or None,
        )
        result['self_reference'] = {
            'matched': bool(self_docs),
            'projects': sorted({d.project.name for d in self_docs}),
        }

        # 挂上已有的核定，专家再点开时看到的是「上次谁定了什么」，
        # 不是从零再判一遍。多条核定保留全部历史，界面默认取最新。
        pids = [r['project_id'] for r in result['ranking']]
        latest = {}
        if pids:
            for rv in CompareReview.objects.filter(
                    query_hash=result['query']['hash'], project_id__in=pids).order_by('created'):
                latest[rv.project_id] = rv
        for item in result['ranking']:
            rv = latest.get(item['project_id'])
            item['review'] = CompareReviewSerializer(rv).data if rv else None
        result['reviewed'] = sum(1 for r in result['ranking'] if r['review'])
        return Response(result)


class CompareReviewViewSet(viewsets.GenericViewSet, mixins.ListModelMixin,
                            mixins.RetrieveModelMixin, mixins.CreateModelMixin):
    """重复建设疑点的专家核定记录。

    只增不改：核定错了要重新核定一条，不能覆盖前一条 —— 审计看的是过程，不是终态。
    """

    queryset = CompareReview.objects.select_related('project', 'reviewer').all()
    permission_classes = [CanReviewCompare]

    def get_serializer_class(self):
        if self.action == 'create':
            return CompareReviewWriteSerializer
        return CompareReviewSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('query_hash'):
            qs = qs.filter(query_hash=p['query_hash'])
        if p.get('project'):
            qs = qs.filter(project_id=p['project'])
        if p.get('verdict'):
            qs = qs.filter(verdict=p['verdict'])
        if p.get('mine') == '1':
            qs = qs.filter(reviewer=self.request.user)
        return qs

    def create(self, request, *args, **kwargs):
        s = CompareReviewWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        rec = CompareReview.objects.create(
            query_hash=d['query_hash'],
            query_name=d.get('query_name') or '',
            query_unit=d.get('query_unit') or '',
            query_year=d.get('query_year'),
            project_id=d['project'],
            verdict=d['verdict'],
            basis=(d.get('basis') or '').strip(),
            findings=d.get('findings') or [],
            reviewer=request.user,
            reviewer_name=getattr(request.user, 'display', '') or request.user.username,
        )
        return Response(CompareReviewSerializer(rec).data, status=status.HTTP_201_CREATED)


class ArchiveStatsView(APIView):
    # 档案总量、年度分布这些数同样是「前面的东西」，一并只给管理员
    permission_classes = [CanReadArchiveDetail]

    def get(self, request):
        projects = ArchiveProject.objects.all()
        files = ArchiveFile.objects.all()
        # 年度全给：砍到 8 年会让更早的档案无法筛选
        by_year = list(projects.exclude(year__isnull=True)
                       .values('year').annotate(n=Count('id')).order_by('-year'))
        by_status = list(projects.values('status').annotate(n=Count('id')))
        agg = files.aggregate(chars=Sum('char_count'), n=Count('id'))
        recent = ArchiveFile.objects.select_related('project').order_by('-created')[:5]
        return Response({
            'projects': projects.count(),
            'files': agg['n'] or 0,
            'chars': agg['chars'] or 0,
            'ready': files.filter(status=ArchiveFile.Status.READY).count(),
            'failed': files.filter(status=ArchiveFile.Status.FAILED).count(),
            'by_year': by_year,
            'by_status': by_status,
            'recent': ArchiveFileBriefSerializer(recent, many=True).data,
        })
