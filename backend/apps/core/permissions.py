from rest_framework.permissions import IsAuthenticated

from .models import User


class IsAdminRole(IsAuthenticated):
    """仅管理员。用于账号管理、模型配置等系统级操作。"""

    message = '仅管理员可执行该操作'

    def has_permission(self, request, view):
        return (super().has_permission(request, view)
                and getattr(request.user, 'role', None) == User.Role.ADMIN)


class IsBusinessUser(IsAuthenticated):
    """管理员以外的四类业务角色。"""

    def has_permission(self, request, view):
        return (super().has_permission(request, view)
                and getattr(request.user, 'role', None) != User.Role.ADMIN)
