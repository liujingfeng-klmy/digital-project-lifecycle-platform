from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (ArchiveFileViewSet, ArchiveProjectViewSet, ArchiveStatsView,
                    CompareReviewViewSet, CompareView)

router = DefaultRouter()
router.register('projects', ArchiveProjectViewSet, basename='archive-project')
router.register('files', ArchiveFileViewSet, basename='archive-file')
router.register('reviews', CompareReviewViewSet, basename='archive-review')

urlpatterns = [
    path('archive/stats/', ArchiveStatsView.as_view(), name='archive-stats'),
    path('archive/compare/', CompareView.as_view(), name='archive-compare'),
    path('archive/', include(router.urls)),
]
