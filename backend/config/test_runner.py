"""测试用的运行器：把 MEDIA_ROOT 指到临时目录。

不这么做会怎样：`manage.py test` 里每个用例都往真实的 `media/` 写文件，
测试库用完就销毁，文件却留在磁盘上 —— 跑几十次之后，`media/projects/`
下会攒出一堆以 `XM2026-001_…` 命名的目录（测试库的编号永远从 001 开始），
点进系统看不到任何记录，却实实在在占着空间。做归档统计时会把人吓一跳。

指到临时目录之后，测试照常读写文件（解析、打包、删项目都要碰磁盘），
跑完连同目录一起删。
"""
import shutil
import tempfile
from pathlib import Path

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class MediaIsolatedRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._media_tmp = tempfile.mkdtemp(prefix='pm-test-media-')
        self._media_override = override_settings(MEDIA_ROOT=Path(self._media_tmp))
        self._media_override.enable()

    def teardown_test_environment(self, **kwargs):
        self._media_override.disable()
        shutil.rmtree(self._media_tmp, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
