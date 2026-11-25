from django.apps import AppConfig


class ProjectConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.project'
    verbose_name = '项目申报与流转'

    def ready(self):
        from . import signals  # noqa: F401  注册删除清理原件的信号
