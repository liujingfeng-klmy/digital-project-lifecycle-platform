"""把原件落到本机下载目录，再交给系统默认程序打开。

为什么由后端动手：浏览器没有调起本地程序的权限，http 页面也不允许跳 file://，
前端无论怎么点都只能落成一个下载文件。而「服务器版」实际是跑在使用者自己电脑上
（启动.command 绑定 127.0.0.1），Django 进程和浏览器在同一个桌面会话里，由它
调 open / startfile，Word 窗口就能直接弹到前台。

只在请求来自本机回环时执行。从别的电脑访问时不要把「在服务器上打开」当成成功
——用户屏幕上不会出现任何窗口，只会以为功能坏了，所以那种情况明确让前端改为下载。
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

LOCAL_ADDRS = {'127.0.0.1', '::1', 'localhost'}
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')
_MAX_STEM = 90


class LocalOpenError(Exception):
    """本机打开失败，message 直接给用户看。

    path 有值时说明文件已经落盘、只是没能自动调起程序，用户手动双击即可。
    """

    def __init__(self, message, path=None):
        super().__init__(message)
        self.path = path


def is_local_request(request):
    """是否允许在本机调起程序打开文件。

    原来只看 REMOTE_ADDR 是否在回环地址里。上 nginx/gunicorn 反代后，
    所有请求的 REMOTE_ADDR 都变成 127.0.0.1，远端用户就能让服务器把文件
    复制到下载目录并调起 Word。改为显式开关：只有桌面模式显式置
    PM_LOCAL_OPEN=1 才启用；且一旦出现反代头（X-Forwarded-For），
    说明请求经过代理，一律不再当作本机直连。
    """
    import os
    if os.environ.get('PM_LOCAL_OPEN', '0') != '1':
        return False
    # 经过反向代理时不认回环地址：那不代表用户就在服务器旁
    if request.META.get('HTTP_X_FORWARDED_FOR'):
        return False
    addr = (request.META.get('REMOTE_ADDR') or '').strip()
    return addr in LOCAL_ADDRS


def _safe_name(filename):
    """清洗成合法文件名。材料原名来自用户上传，可能带路径分隔符或控制字符。"""
    name = _ILLEGAL.sub('_', (filename or '').strip()) or '原件'
    name = name.strip('. ')
    if not name:
        name = '原件'
    stem, dot, ext = name.rpartition('.')
    if not dot:
        return name[:_MAX_STEM]
    return f'{stem[:_MAX_STEM]}.{ext[:12]}'


def download_dir():
    """落点固定为「下载/信息化项目管理平台」，用户想另存或转发时找得到。"""
    base = Path.home() / 'Downloads'
    if not base.is_dir():
        base = Path.home()
    target = base / '信息化项目管理平台'
    target.mkdir(parents=True, exist_ok=True)
    return target


def _unique(path):
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 100):
        cand = path.with_name(f'{stem} ({i}){suffix}')
        if not cand.exists():
            return cand
    return path.with_name(f'{stem} (99){suffix}')


def _launch(path):
    """交给系统默认程序。命令挂住不算失败——文件通常已经递出去了。"""
    system = platform.system()
    if system == 'Windows':
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError as exc:
            raise LocalOpenError(f'调起系统程序失败：{exc}')
        return
    cmd = ['open', str(path)] if system == 'Darwin' else ['xdg-open', str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=15, text=True)
    except FileNotFoundError:
        raise LocalOpenError('当前系统缺少打开文件的命令')
    except subprocess.TimeoutExpired:
        return
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or '').strip().splitlines()
        raise LocalOpenError(lines[-1] if lines else '系统拒绝打开该文件')


def open_on_host(field_file, filename):
    """复制一份到下载目录并打开，返回落地路径。

    只留一份：同名且大小一致时直接打开已有文件，避免每点一次生成一个「(2)」。
    """
    target = download_dir() / _safe_name(filename)
    size = getattr(field_file, 'size', None)
    if target.exists() and size and target.stat().st_size == size:
        _launch(target)
        return target

    try:
        src = Path(field_file.path)
    except (NotImplementedError, AttributeError, ValueError):
        src = None

    target = _unique(target)
    try:
        if src and src.exists():
            shutil.copyfile(src, target)
        else:
            with field_file.open('rb') as fh, open(target, 'wb') as out:
                shutil.copyfileobj(fh, out)
    except FileNotFoundError:
        raise LocalOpenError('原件已丢失，可能被移动或手工删除过')
    except OSError as exc:
        raise LocalOpenError(f'复制到下载目录失败：{exc}')

    try:
        _launch(target)
    except LocalOpenError as exc:
        # 文件已经落盘，用户还能自己双击打开，报错时把位置带上
        raise LocalOpenError(str(exc), path=str(target))
    return target


def respond_local_open(request, field_file, filename):
    """统一的「本机打开」响应。档案页与项目详情页共用。"""
    from rest_framework.response import Response

    if not field_file:
        return Response({'detail': '该材料没有原件'}, status=404)
    if not is_local_request(request):
        return Response(
            {'detail': '当前是从其他电脑访问，无法调起你本机的程序，请改用下载',
             'local': False},
            status=409,
        )
    try:
        path = open_on_host(field_file, filename)
    except LocalOpenError as exc:
        if exc.path:
            return Response({'detail': f'已保存到 {exc.path}，但系统没能自动打开，请手动双击',
                             'local': True, 'path': exc.path}, status=200)
        return Response({'detail': str(exc), 'local': True}, status=400)
    return Response({'local': True, 'opened': True,
                     'path': str(path), 'filename': path.name})
