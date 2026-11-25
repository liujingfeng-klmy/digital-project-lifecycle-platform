from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register('users', views.UserViewSet, basename='user')
# 角色与账号分开：角色在这里建，账号建的时候选角色
router.register('roles', views.RoleViewSet, basename='role')

urlpatterns = [
    path('auth/login', views.LoginView.as_view(), name='login'),
    # 之前只有 refresh token 没有兑换入口，2 小时（原 12 小时）一到就直接掉线，
    # 没保存的表单跟着丢。这里补上标准的续期端点
    path('auth/refresh', TokenRefreshView.as_view(), name='token-refresh'),
    path('auth/me', views.MeView.as_view(), name='me'),
    path('auth/logout', views.LogoutView.as_view(), name='logout'),
    path('auth/change-password', views.ChangePasswordView.as_view(), name='change-password'),
    # 申报单位名录：申报单与账号的「单位」都从这里取值
    path('org-units/', views.OrgUnitListView.as_view(), name='org-units'),
    path('', include(router.urls)),
]
