"""上传配额（审查项 #23，用户拍板 2026-11-23）。

口径：
  · 单用户单次上传总大小不超过 200MB（一次请求里所有文件相加）；
  · 单次最多 10 份文件；
  · 单用户累计不超过 5GB（按库里 uploaded_by=该用户 的原件大小相加，
    含历史版本与评审会材料，因为这些都实际占盘）。

默认值可用环境变量覆盖，便于现场按盘容量调整：
  PM_UPLOAD_MAX_BATCH_MB / PM_UPLOAD_MAX_FILES / PM_UPLOAD_MAX_TOTAL_GB
异步任务改造（Celery）不在本轮范围，本模块只做前置校验。
"""
import os

from django.db.models import Sum

# 单份文件上限由 parsing.ingest 已有的 MAX_BYTES=200MB 负责，这里只管批量与累计。
MAX_FILES = int(os.environ.get('PM_UPLOAD_MAX_FILES', '10'))
MAX_BATCH_MB = int(os.environ.get('PM_UPLOAD_MAX_BATCH_MB', '200'))
MAX_TOTAL_GB = int(os.environ.get('PM_UPLOAD_MAX_TOTAL_GB', '5'))

BATCH_BYTES = MAX_BATCH_MB * 1024 * 1024
TOTAL_BYTES = MAX_TOTAL_GB * 1024 * 1024 * 1024


def _mb(num):
    return f'{num / 1048576:.0f}MB'


def _gb(num):
    return f'{num / (1024 ** 3):.1f}GB'


def used_bytes(user):
    """该用户已占用的原件空间。跨项目、跨材料类型统计。"""
    if not user or not getattr(user, 'pk', None):
        return 0
    # 局部导入避免 archive <-> project 的模块循环
    from apps.project.models import ProjectFile, ReviewFile
    from apps.archive.models import ArchiveFile

    total = 0
    for model in (ProjectFile, ReviewFile, ArchiveFile):
        row = model.objects.filter(uploaded_by=user).aggregate(s=Sum('size'))
        total += row.get('s') or 0
    return total


def check_upload_quota(user, files, *, used=None):
    """校验一次上传。通过返回 None，不通过返回可直接展示给用户的中文文案。

    files 为 UploadedFile 列表；used 可外部传入已算好的占用量，便于一次请求
    多个入口复用（当前各入口独立调用，默认自己去库里查）。
    """
    files = list(files or [])
    if not files:
        return '请选择要上传的文件'
    if len(files) > MAX_FILES:
        return (f'单次最多上传 {MAX_FILES} 份文件，本次选了 {len(files)} 份。'
                '请分批上传后再继续')

    batch = sum(int(getattr(f, 'size', 0) or 0) for f in files)
    if batch > BATCH_BYTES:
        return (f'单次上传总量不能超过 {_mb(BATCH_BYTES)}，本次共 {_mb(batch)}。'
                '请分批上传或压缩后再试')

    occupied = used if used is not None else used_bytes(user)
    if occupied + batch > TOTAL_BYTES:
        free = max(TOTAL_BYTES - occupied, 0)
        return (f'您的累计上传额度为 {_gb(TOTAL_BYTES)}，已用 {_gb(occupied)}，'
                f'剩余 {_mb(free)}；本次 {_mb(batch)} 超出额度。'
                '请删除不再需要的材料或联系管理员清理后重试')
    return None
