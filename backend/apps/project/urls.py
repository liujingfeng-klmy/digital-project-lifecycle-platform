from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (DocTemplateViewSet, InboxView, PendingCountView,
                    ProjectViewSet, StageSlaView, TodoView)

router = DefaultRouter()
router.register('', ProjectViewSet, basename='project')

# 模板库单独一组路由：/api/templates/，仅管理员可访问
tpl_router = DefaultRouter()
tpl_router.register('', DocTemplateViewSet, basename='template')

urlpatterns = [
    path('projects/todos/', TodoView.as_view(), name='project-todos'),
    # 我的两个箱子：待办箱 + 在办箱（存过草稿的件）
    path('projects/inbox/', InboxView.as_view(), name='project-inbox'),
    path('projects/pending/', PendingCountView.as_view(), name='project-pending'),
    # 环节审查时限：管理员在「系统设置」里调整，办理人在详情页看到剩余天数
    path('flow-sla/', StageSlaView.as_view(), name='flow-sla'),
    path('projects/', include(router.urls)),
    path('templates/', include(tpl_router.urls)),
]
