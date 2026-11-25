from django.apps import AppConfig


class ArchiveConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.archive'
    verbose_name = '历史档案库'

    def ready(self):
        from . import signals  # noqa: F401  注册删除清理原件的信号
