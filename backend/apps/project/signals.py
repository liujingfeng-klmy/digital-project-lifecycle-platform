"""删除记录时同步清理磁盘上的原件。

ProjectFile.file 是 FileField，Django 删行不会动文件。原来只有「单份删除」
在视图里手动删了文件，删项目走 CASCADE 时 193 个文件只留下 4 个被引用的，
其余全是孤儿 —— 既占空间，也让「材料是否真的删干净」说不清。

放在信号里而不是 destroy 视图里：无论从接口删、从 admin 删、还是级联删，
都不会漏。文件系统操作不参与数据库事务回滚，极端情况下可能出现「记录回滚、
文件已删」，但那种情况重传一次即可，比留一堆孤儿文件可控。
"""
import os
from pathlib import Path

from django.conf import settings
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Project, ProjectDocument, ProjectFile, ReviewFile


@receiver(post_delete, sender=ProjectFile, dispatch_uid='project_file_cleanup')
def _remove_project_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)


@receiver(post_delete, sender=ProjectDocument, dispatch_uid='project_doc_cleanup')
def _remove_generated_doc(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)


@receiver(post_delete, sender=ReviewFile, dispatch_uid='review_file_cleanup')
def _remove_review_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)


@receiver(post_delete, sender=Project, dispatch_uid='project_folder_cleanup')
def _remove_empty_project_folder(sender, instance, **kwargs):
    """项目删了，把它的空文件夹一起收掉。

    文件由上面几个信号删，但目录不会自己消失。只删空目录：里面若还有
    文件（历史遗留的孤儿），留给 clean_orphan_media 报出来，不在这里
    静默删别人的东西。
    """
    from . import storage

    if instance.review_scan:
        instance.review_scan.delete(save=False)

    root = Path(settings.MEDIA_ROOT)
    folder = root / storage.project_folder(instance)
    if not folder.exists():
        return
    for dirpath, _dirnames, _filenames in os.walk(folder, topdown=False):
        try:
            os.rmdir(dirpath)   # 非空会抛 OSError，跳过
        except OSError:
            pass
