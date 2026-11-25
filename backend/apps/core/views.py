import logging

from django.utils import timezone
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Role, User
from .permissions import IsAdminRole
from .serializers import (ChangePasswordSerializer, LoginSerializer,
                          ResetPasswordSerializer, RoleSerializer,
                          UserCreateSerializer, UserSerializer,
                          UserUpdateSerializer)

log = logging.getLogger(__name__)


class LoginThrottle(AnonRateThrottle):
    """登录失败限流。不设的话可以无限试密码，弱口令几小时就能撞开。

    原来按 IP 计数：NAT/反代后全员共享 10 次/分，早高峰正常登录会被挡；
    改按「用户名 + IP」组合计数，既防单账号爆破，又不会因同 IP 其他人登录
    而误伤。用户名做了归一化，攻击者换大小写也不能绕过计数。
    """

    scope = 'login'

    def get_cache_key(self, request, view):
        username = (request.data.get('username') or '').strip().lower()[:64]
        ident = self.get_ident(request)  # 反代场景下是 X-Forwarded-For 首段
        return self.cache_format % {
            'scope': self.scope,
            'ident': f'{ident}:{username}',
        }


class PasswordThrottle(UserRateThrottle):
    """改密限流。必须按「人」限而不是按「匿名 IP」限——改密请求一定带
    token，AnonRateThrottle 对已认证请求直接放行，等于没设。"""

    scope = 'password'


def issue_tokens(user):
    refresh = RefreshToken.for_user(user)
    # 把口令版本写进 token：改密 / 重置后版本 +1，旧 token 立即失效，
    # 不必等 2 小时自然过期（JWT 无状态，这是最轻量的吊销手段）
    refresh['tv'] = getattr(user, 'token_version', 0)
    return {'refresh': str(refresh), 'access': str(refresh.access_token)}


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        s = LoginSerializer(data=request.data)
        if not s.is_valid():
            # 记失败尝试：事后追查暴力破解，只有这一条线索
            log.warning('登录失败 username=%s ip=%s',
                        (request.data.get('username') or '')[:64],
                        _client_ip(request))
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
        user = s.validated_data['user']
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])
        data = issue_tokens(user)
        data['user'] = UserSerializer(user).data
        if user.must_change_password:
            log.info('首次登录待改密 username=%s', user.username)
        return Response(data)


def _client_ip(request):
    forwarded = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return forwarded or request.META.get('REMOTE_ADDR', '')


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class LogoutView(APIView):
    """登出：把 refresh token 加入黑名单，使其无法再续期。

    access token 仍在其 2 小时寿命内有效（JWT 无状态），但前端登出会清掉
    本地 token；持续有效性靠改密时的 token_version +1 兜底。
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get('refresh') or ''
        if not refresh:
            return Response({'detail': '缺少 refresh token'}, status=400)
        try:
            token = RefreshToken(refresh)
            token.blacklist()
        except Exception:
            # token 已失效/格式错误：视为已经登出，仍返回成功（幂等）
            pass
        return Response({'detail': '已退出登录'})


class ChangePasswordView(APIView):
    """改密。成功即作废旧 token（token_version +1），并下发一套新的。"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [PasswordThrottle]

    def post(self, request):
        s = ChangePasswordSerializer(data=request.data, context={'request': request})
        if not s.is_valid():
            return Response(s.errors, status=status.HTTP_400_BAD_REQUEST)
        user = s.save()
        log.info('修改密码 username=%s', user.username)
        data = issue_tokens(user)
        data['detail'] = '密码已修改，其他设备上的登录已失效'
        data['user'] = UserSerializer(user).data
        return Response(data)


class UserViewSet(viewsets.ModelViewSet):
    """账号管理：仅管理员可增删改查，不做自助注册。"""
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminRole]
    filter_backends = [filters.SearchFilter]
    search_fields = ['username', 'name', 'unit']

    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        if self.action in ('update', 'partial_update'):
            return UserUpdateSerializer
        return UserSerializer

    def create(self, request, *args, **kwargs):
        s = UserCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        s = UserUpdateSerializer(obj, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        # 三条保护：管理员不能把自己锁在门外，也不能把最后一个管理员弄没。
        # 这两个操作都是「点一下就发生、且事后进不去界面」的类型，必须拦死
        # validated_data 里此时还是 role_id（写库时才归一成 role_ref_id），
        # 两个都认，避免保护逻辑在保存前拿不到新角色
        new_role_id = data.get('role_ref_id') or data.get('role_id')
        if obj.pk == request.user.pk:
            if data.get('is_active') is False:
                return Response({'detail': '不能停用自己的账号'}, status=400)
            if new_role_id and new_role_id != obj.role_ref_id:
                return Response({'detail': '不能修改自己的角色，请用另一个管理员账号操作'},
                                status=400)
        losing_admin = (obj.role == User.Role.ADMIN
                        and (data.get('is_active') is False
                             or (new_role_id and obj.role != (
                                 Role.objects.filter(pk=new_role_id)
                                 .values_list('scope', flat=True).first() or ''))))
        if losing_admin and User.objects.filter(role_ref__scope=User.Role.ADMIN,
                                               is_active=True).count() <= 1:
            return Response({'detail': '这是最后一个启用的管理员，不能再停用或改角色'},
                            status=400)
        old_role, old_username = obj.role_label, obj.username
        s.save()
        if old_username != obj.username or old_role != obj.role_label:
            log.info('账号变更 by=%s target=%s %s/%s → %s/%s',
                     request.user.username, obj.pk, old_username, old_role,
                     obj.username, obj.role_label)
        return Response(UserSerializer(obj).data)

    @action(detail=True, methods=['post'], url_path='reset-password')
    def reset_password(self, request, pk=None):
        """重置口令。

        set_password 之后必须把 token_version +1：JWT 是无状态的，不 +1 的话
        对方已登录的设备还能继续用到 token 自然过期，等于密码没换。
        """
        obj = self.get_object()
        s = ResetPasswordSerializer(data=request.data, context={'user': obj})
        s.is_valid(raise_exception=True)
        obj.set_password(s.validated_data['password'])
        obj.must_change_password = True
        obj.token_version = (obj.token_version or 0) + 1
        obj.save(update_fields=['password', 'must_change_password', 'token_version'])
        log.info('重置口令 by=%s target=%s', request.user.username, obj.username)
        return Response({'detail': f'已重置「{obj.display}」的密码，'
                                   '对方下次登录须重新设置'})

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.pk == request.user.pk:
            return Response({'detail': '不能停用自己的账号'}, status=400)
        obj.is_active = False
        obj.save(update_fields=['is_active'])
        # 承办人是真正办理权后，停用账号会让其名下在办件从所有人待办里
        # 消失。停用时统一退回岗位池（handler 置空），由同岗位其他人领取。
        from apps.project.flow import handler_role_of
        from apps.project.models import Project
        # 在办阶段口径与 project/views.py 的 ACTIVE_STAGES 一致：
        # 预审到待验收之间的环节；DRAFT/储备库/终态不涉及承办人办理。
        S = Project.Stage
        active = [S.PRECHECK, S.SUPPLEMENT, S.EXPERT_REVIEW,
                  S.REVIEW_SUPPLEMENT, S.COST_REVIEW, S.COST_SUPPLEMENT,
                  S.FISCAL_REVIEW, S.APPROVAL, S.TENDERING, S.BUILDING,
                  S.TRIAL, S.ACCEPTANCE]
        affected = list(Project.objects.filter(handler=obj, stage__in=active))
        n = len(affected)
        for p in affected:
            p.handler = None
            p.handler_role = handler_role_of(p.stage)
            p.save(update_fields=['handler', 'handler_role', 'updated'])
        log.info('停用账号收回在办件 by=%s target=%s count=%d',
                 request.user.username, obj.username, n)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoleViewSet(viewsets.ModelViewSet):
    """角色管理。全员可读（界面上要显示「承办：张三（初审岗）」），仅管理员可改。

    角色代码由系统生成，管理员只填名称、权限域和说明。代码要进留痕
    （Project.handler_role），让管理员随手改代码会让历史记录指向不存在的角色。
    """

    queryset = Role.objects.all()
    serializer_class = RoleSerializer

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [IsAuthenticated()]
        return [IsAdminRole()]

    def _next_code(self, scope):
        used = set(Role.objects.values_list('code', flat=True))
        n = 2
        while f'{scope}-{n}' in used:
            n += 1
        return f'{scope}-{n}'

    def perform_create(self, serializer):
        scope = serializer.validated_data.get('scope') or Role.Scope.APPLICANT
        # 新角色排在「审核岗」之后、「管理员」之前：这是最常见的挂载位置，
        # 掉在最前面或最后面都会让角色列表看起来乱序
        order = serializer.validated_data.get('order') or 50
        serializer.save(code=self._next_code(scope), order=order)

    def update(self, request, *args, **kwargs):
        obj = self.get_object()
        scope = request.data.get('scope')
        if obj.builtin and scope and scope != obj.scope:
            return Response({'detail': '内置角色的权限域不能改；'
                                       '需要另一类权限域请新建角色'},
                            status=400)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.builtin:
            return Response({'detail': f'「{obj.name}」是内置角色，不能删除；'
                                       '不再使用可以停用'},
                            status=400)
        if obj.users.exists():
            return Response({'detail': f'「{obj.name}」下还有 {obj.users.count()} 个账号，'
                                       '先把账号改到其他角色再删除，或改为停用'},
                            status=400)
        return super().destroy(request, *args, **kwargs)


class OrgUnitListView(APIView):
    """申报单位下拉数据。所有登录用户可读：申报人要选，管理员建账号也要选。"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from . import org_units

        include_inactive = (request.query_params.get('all') == '1'
                            and request.user.role == User.Role.ADMIN)
        groups = org_units.list_grouped(include_inactive=include_inactive)
        return Response({
            'groups': groups,
            'count': sum(len(g['items']) for g in groups),
        })
