"""带口令版本的 JWT 认证。

标准 SimpleJWT 的 token 一旦签发，到期前无法作废。改密、账号被重置密码后，
旧 token 仍然能用到自然过期为止。这里在 token 里加一个 tv（token version）声明，
认证时与库里的 token_version 比对：不相等即视为已吊销。

兼容处理：升级前签发的旧 token 没有 tv 声明（取到 None），放行一次，
等它自然过期即可，避免升级瞬间把所有人踢下线。
"""
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication


class VersionedJWTAuthentication(JWTAuthentication):
    # 首次登录须改密的账号，服务端只放行这几个端点：
    # 读自己的信息、改密、续期。其余任何读写一律 403，
    # 杜绝绕过前端直接调 API 继续用默认口令作业。
    _MUST_CHANGE_ALLOWED = ('/api/auth/me', '/api/auth/change-password',
                            '/api/auth/refresh')

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        # 角色停用 = 即时收回权限。已签发的 token 不必等自然过期，
        # 这里每次请求都查一次角色 active；停用后立刻 403。
        # 无角色的历史账号放行（role_ref 为空不参与停用判断）。
        if user.role_ref_id and not user.role_ref.active:
            raise PermissionDenied('账号所属角色已停用，请联系管理员')
        if getattr(user, 'must_change_password', False):
            path = request.path
            if not any(path.startswith(p) for p in self._MUST_CHANGE_ALLOWED):
                raise PermissionDenied('首次登录须先修改密码')
        return result

    def get_user(self, validated_token):
        user = super().get_user(validated_token)   # 内含 is_active 校验
        token_version = validated_token.get('tv')
        if token_version is not None and int(token_version) != user.token_version:
            raise AuthenticationFailed('密码已变更，请重新登录')
        return user
