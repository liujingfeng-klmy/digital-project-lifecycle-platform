"""本机打开原件：落到下载目录 + 调起系统程序，只在从本机访问时执行。"""
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.localopen import LocalOpenError
from apps.core.models import User
from apps.project.models import Project, ProjectFile

from .models import ArchiveFile, ArchiveProject

DOCX_BYTES = b'PK\x03\x04' + b'fake-docx-content' * 8


class LocalOpenMixin:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        override.enable()
        self.addCleanup(override.disable)

        self.downloads = Path(self.tmp.name) / 'Downloads'
        self.downloads.mkdir()

        # 本机打开改为显式开关控制：测试里模拟桌面模式开启该开关
        env = mock.patch.dict(os.environ, {'PM_LOCAL_OPEN': '1'})
        env.start()
        self.addCleanup(env.stop)

        patcher = mock.patch('apps.core.localopen.download_dir',
                             return_value=self.downloads)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.launched = []
        launch = mock.patch('apps.core.localopen._launch',
                            side_effect=lambda p: self.launched.append(Path(p)))
        launch.start()
        self.addCleanup(launch.stop)

    def client_for(self, user, addr='127.0.0.1'):
        client = APIClient()
        client.force_authenticate(user)
        client.defaults['REMOTE_ADDR'] = addr
        return client


class ArchiveOpenTest(LocalOpenMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('open_admin', password='Tmp#12345',
                                             role=User.Role.ADMIN)
        cls.applicant = User.objects.create_user('open_applicant', password='Tmp#12345',
                                                 role=User.Role.APPLICANT)

    def setUp(self):
        super().setUp()
        project = ArchiveProject.objects.create(name='本机打开测试', unit='测试单位')
        self.rec = ArchiveFile.objects.create(
            project=project,
            file=SimpleUploadedFile('可研报告.docx', DOCX_BYTES),
            filename='可研报告.docx',
            size=len(DOCX_BYTES),
            status=ArchiveFile.Status.READY,
        )

    def test_local_request_saves_and_launches(self):
        resp = self.client_for(self.admin).post(f'/api/archive/files/{self.rec.pk}/open/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data['opened'])
        target = Path(resp.data['path'])
        self.assertEqual(target.name, '可研报告.docx')
        self.assertEqual(target.read_bytes(), DOCX_BYTES)
        self.assertEqual(self.launched, [target])

    def test_second_click_reuses_same_file(self):
        client = self.client_for(self.admin)
        client.post(f'/api/archive/files/{self.rec.pk}/open/')
        resp = client.post(f'/api/archive/files/{self.rec.pk}/open/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['filename'], '可研报告.docx')
        self.assertEqual(len(list(self.downloads.iterdir())), 1)

    def test_remote_request_asks_frontend_to_download(self):
        resp = self.client_for(self.admin, addr='192.168.1.20').post(
            f'/api/archive/files/{self.rec.pk}/open/')
        self.assertEqual(resp.status_code, 409)
        self.assertFalse(resp.data['local'])
        self.assertEqual(self.launched, [])
        self.assertEqual(list(self.downloads.iterdir()), [])

    def test_applicant_forbidden(self):
        resp = self.client_for(self.applicant).post(
            f'/api/archive/files/{self.rec.pk}/open/')
        self.assertEqual(resp.status_code, 403)

    def test_launch_failure_still_reports_landed_path(self):
        with mock.patch('apps.core.localopen._launch',
                        side_effect=LocalOpenError('没有可用程序')):
            resp = self.client_for(self.admin).post(
                f'/api/archive/files/{self.rec.pk}/open/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('手动双击', resp.data['detail'])
        self.assertTrue(Path(resp.data['path']).exists())


class ProjectFileOpenTest(LocalOpenMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('pfile_admin', password='Tmp#12345',
                                            role=User.Role.ADMIN)

    def setUp(self):
        super().setUp()
        project = Project.objects.create(code='XM2026-950', name='材料打开测试',
                                         unit='测试单位', applicant=self.admin,
                                         stage=Project.Stage.DRAFT)
        self.rec = ProjectFile.objects.create(
            project=project,
            file=SimpleUploadedFile('申报书.docx', DOCX_BYTES),
            filename='申报书.docx',
            size=len(DOCX_BYTES),
            status=ProjectFile.Status.READY,
        )

    def test_opens_project_file(self):
        resp = self.client_for(self.admin).post(
            f'/api/projects/{self.rec.project_id}/files/{self.rec.pk}/open/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Path(resp.data['path']).read_bytes(), DOCX_BYTES)
