"""第一批修复的回归测试：前置校验（doc_type / 承办人 / 草稿隔离 / 归档裁剪）。

自包含，不依赖 tests.py 的 FlowTestBase —— 只建本测试需要的最小场景。
"""
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Role, User
from . import flow
from .models import PrecheckReport, Project, ProjectFile
from .views import APPLICANT_DOC_TYPES


class FixB1Base(TestCase):
    def setUp(self):
        for code, label in (('applicant', '申报单位'), ('reviewer', '初审岗'),
                            ('auditor', '审核岗'), ('admin', '管理员')):
            Role.objects.get_or_create(
                code=code, defaults={'name': label, 'scope': code, 'builtin': True})
        self.applicant = User.objects.create_user(
            username='a1', role='applicant', unit='教育局')
        self.reviewer = User.objects.create_user(
            username='r1', role='reviewer')
        self.reviewer2 = User.objects.create_user(
            username='r2', role='reviewer')
        self.auditor = User.objects.create_user(
            username='u1', role='auditor')
        self.admin = User.objects.create_user(
            username='m1', role='admin')

    def client_for(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def make_project(self, stage=Project.Stage.DRAFT):
        return Project.objects.create(
            name='测试项目', unit='教育局', applicant=self.applicant,
            stage=stage, handler_role=flow.handler_role_of(stage))

    def upload(self, user, project, doc_type='feasibility', content='正文' * 200,
               name='可研.txt'):
        return self.client_for(user).post(f'/api/projects/{project.id}/files/', {
            'doc_type': doc_type,
            'file': SimpleUploadedFile(name, content.encode('utf-8')),
        }, format='multipart')


class DocTypeWhitelistTest(FixB1Base):
    """#1/#6：申报单位不能靠声明 doc_type 混入局方/第三方材料。"""

    def test_applicant_cannot_upload_cost_report(self):
        p = self.make_project()
        r = self.upload(self.applicant, p, doc_type='cost_report', name='假评估报告.pdf')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(p.files.count(), 0)

    def test_applicant_cannot_upload_data_collect_or_accept_opinion(self):
        p = self.make_project()
        for t in ('data_collect', 'accept_opinion'):
            r = self.upload(self.applicant, p, doc_type=t, name=f'{t}.txt')
            self.assertEqual(r.status_code, 403, t)
        self.assertEqual(p.files.count(), 0)

    def test_applicant_can_still_upload_own_types(self):
        p = self.make_project()
        for i, t in enumerate(sorted(APPLICANT_DOC_TYPES)):
            r = self.upload(self.applicant, p, doc_type=t, name=f'{t}_{i}.txt',
                            content=f'{t} 正文'.join(['内容'] * 100))
            self.assertEqual(r.status_code, 200, t)
        self.assertEqual(p.files.count(), len(APPLICANT_DOC_TYPES))

    def test_auditor_can_upload_real_cost_report(self):
        p = self.make_project(Project.Stage.COST_REVIEW)
        p.handler = self.auditor
        p.save(update_fields=['handler'])
        r = self.upload(self.auditor, p, doc_type='cost_report', name='真评估报告.pdf')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(p.files.first().doc_type, 'cost_report')

    def test_applicant_cannot_retype_file_as_cost_report(self):
        p = self.make_project()
        r = self.upload(self.applicant, p, doc_type='feasibility')
        self.assertEqual(r.status_code, 200)
        fid = p.files.first().id
        r = self.client_for(self.applicant).patch(
            f'/api/projects/{p.id}/files/{fid}/type/', {'doc_type': 'cost_report'},
            format='json')
        self.assertEqual(r.status_code, 403)
        p.files.first().refresh_from_db()
        self.assertEqual(p.files.first().doc_type, 'feasibility')


class DeleteFileTest(FixB1Base):
    """#4：申报单位不能删掉审核岗上传的费用评估报告。"""

    def test_applicant_cannot_delete_auditor_report_in_cost_supplement(self):
        p = self.make_project(Project.Stage.COST_SUPPLEMENT)
        rec = ProjectFile.objects.create(
            project=p, file='projects/x/report.pdf', filename='评估报告.pdf',
            doc_type=ProjectFile.DocType.COST_REPORT, uploaded_by=self.auditor,
            status=ProjectFile.Status.READY)
        r = self.client_for(self.applicant).delete(f'/api/projects/{p.id}/files/{rec.id}/')
        self.assertEqual(r.status_code, 403)
        self.assertTrue(ProjectFile.objects.filter(pk=rec.id).exists())

    def test_applicant_can_delete_own_material(self):
        p = self.make_project()
        self.upload(self.applicant, p)
        rec = p.files.first()
        r = self.client_for(self.applicant).delete(f'/api/projects/{p.id}/files/{rec.id}/')
        self.assertEqual(r.status_code, 204)


class DraftIsolationTest(FixB1Base):
    """#3：草稿对局内角色的所有读入口都不可见。"""

    def make_file(self, p):
        return ProjectFile.objects.create(
            project=p, file='projects/x/doc.txt', filename='doc.txt',
            doc_type=ProjectFile.DocType.FEASIBILITY, uploaded_by=self.applicant,
            status=ProjectFile.Status.READY, text='草稿正文')

    def test_reviewer_cannot_read_draft_file_text(self):
        p = self.make_project()
        f = self.make_file(p)
        r = self.client_for(self.reviewer).get(f'/api/projects/{p.id}/files/{f.id}/text/')
        self.assertEqual(r.status_code, 404)

    def test_reviewer_cannot_read_draft_file_raw(self):
        p = self.make_project()
        f = self.make_file(p)
        r = self.client_for(self.reviewer).get(f'/api/projects/{p.id}/files/{f.id}/raw/')
        self.assertEqual(r.status_code, 404)

    def test_reviewer_cannot_read_draft_retrieve(self):
        p = self.make_project()
        r = self.client_for(self.reviewer).get(f'/api/projects/{p.id}/')
        self.assertEqual(r.status_code, 404)

    def test_reviewer_can_read_submitted_project(self):
        p = self.make_project(Project.Stage.PRECHECK)
        f = self.make_file(p)
        r = self.client_for(self.reviewer).get(f'/api/projects/{p.id}/files/{f.id}/text/')
        self.assertEqual(r.status_code, 200)


class HandlerCheckTest(FixB1Base):
    """#7：文书与评审材料等旁路写操作必须校验承办人。"""

    def test_previous_handler_cannot_generate_precheck_notice(self):
        p = self.make_project(Project.Stage.PRECHECK)
        p.handler = self.reviewer2
        p.save(update_fields=['handler'])
        PrecheckReport.objects.create(
            project=p, round=0, level='rule', verdict='attention',
            items=[{'key': 'k1', 'title': '问题描述足够长'}])
        r = self.client_for(self.reviewer).post(
            f'/api/projects/{p.id}/docs/precheck-notice/', {'keys': ['k1']}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_current_handler_can_generate_precheck_notice(self):
        p = self.make_project(Project.Stage.PRECHECK)
        p.handler = self.reviewer
        p.save(update_fields=['handler'])
        PrecheckReport.objects.create(
            project=p, round=0, level='rule', verdict='attention',
            items=[{'key': 'k1', 'title': '问题描述足够长'}])
        r = self.client_for(self.reviewer).post(
            f'/api/projects/{p.id}/docs/precheck-notice/', {'keys': ['k1']}, format='json')
        # key 'k1' 不在候选池里，会返回 400；这里只证明承办人没被拦在门外
        self.assertNotEqual(r.status_code, 403)

    def test_previous_handler_cannot_upload_review_files(self):
        p = self.make_project(Project.Stage.EXPERT_REVIEW)
        p.handler = self.reviewer2
        p.save(update_fields=['handler'])
        r = self.client_for(self.reviewer).post(f'/api/projects/{p.id}/review-files/', {
            'kind': 'expert',
            'file': SimpleUploadedFile('意见表.pdf', b'%PDF-1.4 test', 'application/pdf'),
        }, format='multipart')
        self.assertEqual(r.status_code, 403)

    def test_admin_bypasses_handler_check(self):
        p = self.make_project(Project.Stage.PRECHECK)
        p.handler = self.reviewer2
        p.save(update_fields=['handler'])
        r = self.client_for(self.admin).post(
            f'/api/projects/{p.id}/docs/precheck-notice/', {'keys': ['k1']}, format='json')
        self.assertNotEqual(r.status_code, 403)


class ArchiveCropTest(FixB1Base):
    """#2：归档包按角色裁剪，对齐详情接口口径。"""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp()
        self._ov = override_settings(DATA_DIR=Path(self._tmp))
        self._ov.enable()

    def tearDown(self):
        self._ov.disable()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def _download(self, user, p):
        r = self.client_for(user).get(f'/api/projects/{p.id}/archive/')
        self.assertEqual(r.status_code, 200)
        blob = b''.join(r.streaming_content) if r.streaming else r.content
        return zipfile.ZipFile(io.BytesIO(blob))

    def test_applicant_package_crops_internal_fields(self):
        p = self.make_project(Project.Stage.ACCEPTED)
        p.dup_check = {'level': 'high', 'level_label': '高度相似',
                       'ranking': [{'name': '别家项目', 'snippet': '别家原文'}]}
        p.save(update_fields=['dup_check'])
        PrecheckReport.objects.create(
            project=p, round=0, level='rule', verdict='block',
            items=[{'code': 'x', 'title': '内部检查项'}],
            findings=[{'text': 'AI 引用的原文片段'}])
        with self._download(self.applicant, p) as zf:
            info = json.loads(zf.read('04_过程/项目信息.json'))
            concl = json.loads(zf.read('04_过程/结论与意见.json'))
        self.assertNotIn('ranking', info['dup_check'])
        self.assertEqual(info['dup_check']['level'], 'high')
        row = concl['预审结果'][0]
        self.assertNotIn('items', row)
        self.assertNotIn('findings', row)

    def test_reviewer_package_keeps_internal_fields(self):
        p = self.make_project(Project.Stage.ACCEPTED)
        PrecheckReport.objects.create(
            project=p, round=0, level='rule', verdict='block',
            items=[{'code': 'x', 'title': '内部检查项'}], findings=[{'text': '片段'}])
        with self._download(self.reviewer, p) as zf:
            concl = json.loads(zf.read('04_过程/结论与意见.json'))
        row = concl['预审结果'][0]
        self.assertIn('items', row)
        self.assertIn('findings', row)

    def test_archive_gate_cannot_be_bypassed_by_role_or_direct_call(self):
        """验收门槛在服务端：任何角色、任何调用入口都拿不到半套材料的包。"""
        from .archiving import ArchiveError, export_project_package

        p = self.make_project(Project.Stage.DRAFT)
        for stage in (Project.Stage.DRAFT, Project.Stage.PRECHECK,
                      Project.Stage.ACCEPTANCE, Project.Stage.REJECTED):
            p.stage = stage
            p.save(update_fields=['stage', 'updated'])
            # API 层：管理员也不能绕
            r = self.client_for(self.admin).get(f'/api/projects/{p.id}/archive/')
            self.assertEqual(r.status_code, 403, (stage, r.data))
            # 服务层：直接调导出函数同样被拦
            with self.assertRaises(ArchiveError):
                export_project_package(p, self.admin)
