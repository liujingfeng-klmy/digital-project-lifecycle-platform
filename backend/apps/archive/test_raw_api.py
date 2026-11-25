"""原件流接口：能看详情的人才能看原件，且返回的就是上传的那几个字节。"""
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import User

from .models import ArchiveFile, ArchiveProject

PDF_BYTES = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'


class ArchiveRawTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('raw_admin', password='Tmp#12345',
                                             role=User.Role.ADMIN)
        cls.applicant = User.objects.create_user('raw_applicant', password='Tmp#12345',
                                                 role=User.Role.APPLICANT)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        override.enable()
        self.addCleanup(override.disable)

        project = ArchiveProject.objects.create(name='原件测试项目', unit='测试单位')
        self.rec = ArchiveFile.objects.create(
            project=project,
            file=SimpleUploadedFile('方案.pdf', PDF_BYTES, content_type='application/pdf'),
            filename='方案.pdf',
            size=len(PDF_BYTES),
            status=ArchiveFile.Status.READY,
        )

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_admin_gets_original_bytes(self):
        resp = self.client_for(self.admin).get(f'/api/archive/files/{self.rec.pk}/raw/')
        self.assertEqual(resp.status_code, 200)
        body = b''.join(resp.streaming_content)
        self.assertEqual(body, PDF_BYTES)
        self.assertEqual(resp.headers['Content-Type'], 'application/pdf')
        self.assertIn('inline', resp.headers['Content-Disposition'])

    def test_applicant_forbidden(self):
        resp = self.client_for(self.applicant).get(f'/api/archive/files/{self.rec.pk}/raw/')
        self.assertEqual(resp.status_code, 403)

    def test_missing_file_returns_404(self):
        Path(self.rec.file.path).unlink()
        resp = self.client_for(self.admin).get(f'/api/archive/files/{self.rec.pk}/raw/')
        self.assertEqual(resp.status_code, 404)
        self.assertIn('原件已丢失', resp.data['detail'])
