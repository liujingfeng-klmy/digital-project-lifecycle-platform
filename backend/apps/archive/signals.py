"""档案材料删除时同步清理原件（与 project 的 ProjectFile 同一套处理）。

档案删除有两条路径：删单份材料、删整个历史项目（CASCADE）。
前者原来在视图里手动 os.remove，后者在 ArchiveProject.destroy 里手动删；
合并（merge）只改 project 外键，不动文件。改成一个信号后三条路径统一，
视图里的手工清理可以去掉。
"""
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import ArchiveFile


@receiver(post_delete, sender=ArchiveFile, dispatch_uid='archive_file_cleanup')
def _remove_archive_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
