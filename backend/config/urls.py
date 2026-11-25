from pathlib import Path

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve

SPA_DIR = Path(settings.BASE_DIR) / 'spa'


def spa_index(request):
    index = SPA_DIR / 'index.html'
    if not index.exists():
        return HttpResponse(
            '前端尚未构建。请在 frontend 目录执行 npm run build，或使用开发模式 '
            '（frontend 目录执行 npm run dev，访问 127.0.0.1:5173）。',
            status=503,
        )
    resp = HttpResponse(index.read_bytes(), content_type='text/html; charset=utf-8')
    # 首页是构建产物的入口，必须每次回源：否则浏览器拿着旧 index.html
    # 去要已被清掉的旧哈希资源，或者迟迟看不到新样式
    resp['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp['Pragma'] = 'no-cache'
    resp['Expires'] = '0'
    return resp


def spa_asset(request, path):
    """构建产物：文件名带哈希，本可长缓存。但开发期反复重建，
    浏览器/预览壳里留着旧 HTML 时会把旧样式一直用下去，
    这里统一要求回源校验，改完刷新就能看到。"""
    resp = serve(request, path, document_root=SPA_DIR / 'assets')
    resp['Cache-Control'] = 'no-cache, must-revalidate'
    return resp


urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('apps.core.urls')),
    path('api/', include('apps.aicore.urls')),
    path('api/', include('apps.archive.urls')),
    path('api/', include('apps.project.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if (SPA_DIR / 'index.html').exists():
    urlpatterns += [
        re_path(r'^assets/(?P<path>.*)$', spa_asset),
        re_path(r'^(?!api/|admin/|media/|static/).*$', spa_index),
    ]
