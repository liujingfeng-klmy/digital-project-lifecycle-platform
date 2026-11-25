"""删除项目的权限与留痕归档。

重点是三条口径：管理员能删非草稿但必须先归档、申报单位只能删自己的草稿、
归档失败绝不允许删成「无痕」。
"""
import json
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import User

from .archiving import ArchiveError
from .models import Project, ProjectEvent


class ProjectDeleteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('del_admin', password='Tmp#12345',
                                             role=User.Role.ADMIN)
        cls.applicant = User.objects.create_user('del_applicant', password='Tmp#12345',
                                                 role=User.Role.APPLICANT)
        cls.reviewer = User.objects.create_user('del_reviewer', password='Tmp#12345',
                                                role=User.Role.REVIEWER)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(DATA_DIR=Path(self.tmp.name))
        override.enable()
        self.addCleanup(override.disable)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def make_project(self, stage=Project.Stage.ACCEPTED, code='XM2026-901'):
        project = Project.objects.create(code=code, name='归档测试项目', unit='测试单位',
                                         applicant=self.applicant, stage=stage)
        ProjectEvent.objects.create(project=project, action='create',
                                    action_label='新建申报', to_stage=Project.Stage.DRAFT)
        return project

    def test_admin_deletes_submitted_project_with_archive(self):
        project = self.make_project()
        resp = self.client_for(self.admin).delete(f'/api/projects/{project.pk}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(Project.objects.filter(pk=project.pk).exists())

        zips = list((Path(self.tmp.name) / 'deleted').glob('*.zip'))
        self.assertEqual(len(zips), 1)
        with zipfile.ZipFile(zips[0]) as zf:
            payload = json.loads(zf.read('留痕.json').decode('utf-8'))
        self.assertEqual(payload['project']['code'], 'XM2026-901')
        self.assertEqual(payload['archived_by']['role'], 'admin')
        self.assertEqual(len(payload['events']), 1)

    def test_applicant_cannot_delete_submitted_project(self):
        project = self.make_project()
        resp = self.client_for(self.applicant).delete(f'/api/projects/{project.pk}/')
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())
        self.assertFalse((Path(self.tmp.name) / 'deleted').exists())

    def test_other_roles_cannot_delete(self):
        project = self.make_project(stage=Project.Stage.DRAFT, code='XM2026-902')
        resp = self.client_for(self.reviewer).delete(f'/api/projects/{project.pk}/')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())

    def test_applicant_deletes_own_draft_without_archive(self):
        project = self.make_project(stage=Project.Stage.DRAFT, code='XM2026-903')
        resp = self.client_for(self.applicant).delete(f'/api/projects/{project.pk}/')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Project.objects.filter(pk=project.pk).exists())
        self.assertFalse((Path(self.tmp.name) / 'deleted').exists())

    def test_archive_failure_aborts_delete(self):
        project = self.make_project(code='XM2026-904')
        with mock.patch('apps.project.views.export_project_archive',
                        side_effect=ArchiveError('磁盘已满')):
            resp = self.client_for(self.admin).delete(f'/api/projects/{project.pk}/')
        self.assertEqual(resp.status_code, 500)
        self.assertIn('磁盘已满', resp.data['detail'])
        self.assertTrue(Project.objects.filter(pk=project.pk).exists())
