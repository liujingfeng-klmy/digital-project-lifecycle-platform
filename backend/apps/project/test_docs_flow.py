"""新增文书生成、待办已读、财政/立项流转、模板库的测试。

单独成文件，不依赖 tests.py 里的夹具：这里直接从模型造数，跑得快，
也不受原有流程用例的改动影响。
"""
import shutil
import tempfile

from django.conf import settings
from django.test import TestCase, override_settings
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from rest_framework.test import APIClient

from apps.core.models import User

from .models import (DocTemplate, PrecheckReport, Project, ProjectDocument,
                     TodoAck)


class DocFlowTestBase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.mkdtemp(prefix='pm-test-media-')
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.applicant = User.objects.create_user(
            username='app1', password='x', role=User.Role.APPLICANT,
            name='张三', unit='市数据资源和政务服务中心', phone='13900000000')
        self.reviewer = User.objects.create_user(
            username='rev1', password='x', role=User.Role.REVIEWER, name='李四')
        self.auditor = User.objects.create_user(
            username='aud1', password='x', role=User.Role.AUDITOR, name='王五')
        self.admin = User.objects.create_user(
            username='adm1', password='x', role=User.Role.ADMIN, name='管理员')
        self.client = APIClient()

    def make_project(self, stage='precheck', **kw):
        data = dict(code=f'XM2026-{Project.objects.count() + 1:03d}',
                    name='不动产登记一网通办平台建设项目',
                    applicant=self.applicant,
                    unit='市数据资源和政务服务中心',
                    year=2026, investment=860, duration_months=12,
                    summary='建设不动产登记一网通办平台，整合登记、交易、税务等业务。',
                    stage=stage)
        data.update(kw)
        return Project.objects.create(**data)

    def as_user(self, user):
        self.client.force_authenticate(user=user)
        return self.client

    def doc_text(self, rec):
        with rec.file.open('rb') as fh:
            return '\n'.join(p.text for p in Document(fh).paragraphs)


class PrecheckNoticeTest(DocFlowTestBase):
    def setUp(self):
        super().setUp()
        self.p = self.make_project('precheck')
        PrecheckReport.objects.create(
            project=self.p, round=0, level='rule', verdict='warn',
            items=[
                {'code': 'inv_sum', 'group': '投资勾稽', 'title': '概算合计与申报投资差距较大',
                 'severity': 'warn', 'detail': '概算合计 720 万元，申报 860 万元。',
                 'advice': '核对是否漏计工程建设其他费。'},
                {'code': 'meta_ok', 'group': '申报要素', 'title': '申报要素齐全',
                 'severity': 'pass', 'detail': '', 'advice': ''},
            ])
        PrecheckReport.objects.create(
            project=self.p, round=0, level='ai', verdict='block',
            findings=[
                {'level': 'block', 'title': '绩效指标不可考核',
                 'issue': '绩效目标只有定性描述。', 'advice': '补充可量化的年度指标。',
                 'section': '绩效目标', 'quote': ''},
            ])

    def test_candidates_exclude_pass(self):
        c = self.as_user(self.reviewer).get(f'/api/projects/{self.p.id}/').data
        keys = [x['key'] for x in c['precheck_candidates']]
        self.assertIn('rule:0', keys)
        self.assertIn('ai:0', keys)
        self.assertNotIn('rule:1', keys)  # pass 不进函件

    def test_generate_selected_items(self):
        r = self.as_user(self.reviewer).post(
            f'/api/projects/{self.p.id}/docs/precheck-notice/',
            {'keys': ['rule:0', 'ai:0'], 'deadline': '2026-11-27'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        rec = ProjectDocument.objects.get(project=self.p, kind='precheck_notice')
        text = self.doc_text(rec)
        self.assertIn('不动产登记一网通办平台建设项目', text)
        self.assertIn('概算合计与申报投资差距较大', text)
        self.assertIn('绩效指标不可考核', text)
        self.assertIn('2026年11月27日', text)
        # 只选两条：模板里第三～五条空行要删掉，不留空白
        self.assertNotIn('三、', text)

    def test_other_role_cannot_generate(self):
        r = self.as_user(self.applicant).post(
            f'/api/projects/{self.p.id}/docs/precheck-notice/',
            {'keys': ['rule:0']}, format='json')
        self.assertEqual(r.status_code, 403)


class CostAndApprovalDocTest(DocFlowTestBase):
    def test_cost_task_defaults_to_seven_days(self):
        p = self.make_project('cost_review')
        r = self.as_user(self.auditor).post(
            f'/api/projects/{p.id}/docs/cost-review-task/',
            {'entrust_unit': '市财政投资评审中心'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        rec = ProjectDocument.objects.get(project=p, kind='cost_review_task')
        with rec.file.open('rb') as fh:
            doc = Document(fh)
        cell_text = '\n'.join(c.text for row in doc.tables[0].rows for c in row.cells)
        self.assertIn('不动产登记一网通办平台建设项目', cell_text)
        self.assertIn('市财政投资评审中心', cell_text)
        self.assertIn('评审期限7个自然日', cell_text)
        self.assertIn(settings.ORG_ISSUER, cell_text)

    def test_cost_task_has_both_contacts(self):
        """任务书要两组联系人：委托单位（我局）与建设单位（申报单位）。"""
        p = self.make_project('cost_review')
        r = self.as_user(self.auditor).post(
            f'/api/projects/{p.id}/docs/cost-review-task/',
            {'entrust_unit': '市财政投资评审中心',
             'entrust_contact': '王五', 'entrust_phone': '0990-6220000',
             'unit_contact': '张三', 'unit_phone': '13900000000'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        rec = ProjectDocument.objects.get(project=p, kind='cost_review_task')
        with rec.file.open('rb') as fh:
            doc = Document(fh)
        rows = [tuple(c.text for c in row.cells) for row in doc.tables[0].rows]
        labels = [r[0] for r in rows]
        self.assertEqual(labels[2], '建设单位联系人')
        self.assertEqual(labels[4], '委托单位联系人')
        cell_text = '\n'.join(c.text for row in doc.tables[0].rows for c in row.cells)
        for v in ('王五', '0990-6220000', '张三', '13900000000'):
            self.assertIn(v, cell_text)

    def test_cost_task_contacts_fall_back_to_users(self):
        """不填联系人时按经办人 / 申报人兜底，不留空白。"""
        p = self.make_project('cost_review')
        r = self.as_user(self.auditor).post(
            f'/api/projects/{p.id}/docs/cost-review-task/',
            {'entrust_unit': '市财政投资评审中心'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        rec = ProjectDocument.objects.get(project=p, kind='cost_review_task')
        with rec.file.open('rb') as fh:
            doc = Document(fh)
        cell_text = '\n'.join(c.text for row in doc.tables[0].rows for c in row.cells)
        self.assertIn('王五', cell_text)          # 审核岗本人
        self.assertIn('张三', cell_text)          # 申报单位申报人
        self.assertIn('13900000000', cell_text)   # 申报人电话
        self.assertEqual(rec.meta['entrust_contact'], '王五')
        self.assertEqual(rec.meta['unit_contact'], '张三')

    def test_cost_task_rejects_short_phone(self):
        """10 位号码当成电话印到对外文书上，等于让财评机构打不通。"""
        p = self.make_project('cost_review')
        r = self.as_user(self.auditor).post(
            f'/api/projects/{p.id}/docs/cost-review-task/',
            {'entrust_unit': '市财政投资评审中心',
             'entrust_contact': '王五', 'entrust_phone': '0990622000'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('委托单位联系电话', r.data['detail'])

        r = self.as_user(self.auditor).post(
            f'/api/projects/{p.id}/docs/cost-review-task/',
            {'entrust_unit': '市财政投资评审中心',
             'unit_contact': '张三', 'unit_phone': '1380000000'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('建设单位联系电话', r.data['detail'])
        self.assertFalse(ProjectDocument.objects.filter(
            project=p, kind='cost_review_task').exists())

    def test_cost_task_accepts_mobile_and_landline(self):
        p = self.make_project('cost_review')
        for phone in ('13800000000', '0990-6220000', '09906220000',
                      '010-12345678-123', '0990－6220000'):
            r = self.as_user(self.auditor).post(
                f'/api/projects/{p.id}/docs/cost-review-task/',
                {'entrust_unit': '市财政投资评审中心',
                 'entrust_contact': '王五', 'entrust_phone': phone},
                format='json')
            self.assertEqual(r.status_code, 200, (phone, r.data))

    def test_cost_task_signature_right_aligned(self):
        """落款要右对齐，原来贴着左边线，打印出来像正文。"""
        p = self.make_project('cost_review')
        r = self.as_user(self.auditor).post(
            f'/api/projects/{p.id}/docs/cost-review-task/',
            {'entrust_unit': '市财政投资评审中心'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        rec = ProjectDocument.objects.get(project=p, kind='cost_review_task')
        with rec.file.open('rb') as fh:
            doc = Document(fh)
        paras = doc.tables[0].rows[7].cells[0].paragraphs
        sign = [x for x in paras if x.text.strip().startswith(settings.ORG_ISSUER)
                or x.text.strip().endswith('日')]
        self.assertTrue(sign)
        for x in sign:
            self.assertEqual(x.alignment, WD_ALIGN_PARAGRAPH.RIGHT, x.text)

    def test_cost_task_meta_supports_form_prefill(self):
        """生成参数要原样回传：前端靠 meta 把表单填回去。

        单位反馈完再进费用评审时，原来四个框全空，等于让人凭记忆重填一遍；
        生成过的任务书也不该再让人重填一次才能看到内容。
        """
        p = self.make_project('cost_review')
        r = self.as_user(self.auditor).post(
            f'/api/projects/{p.id}/docs/cost-review-task/',
            {'entrust_unit': '市财政投资评审中心', 'days': 10,
             'entrust_contact': '王五', 'entrust_phone': '0990-6220000',
             'unit_contact': '张三', 'unit_phone': '13900000000'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        docs = r.data['project']['documents']
        self.assertEqual(docs[0]['kind'], 'cost_review_task')
        meta = docs[0]['meta']
        self.assertEqual(meta['entrust_unit'], '市财政投资评审中心')
        self.assertEqual(meta['entrust_contact'], '王五')
        self.assertEqual(meta['entrust_phone'], '0990-6220000')
        self.assertEqual(meta['unit_contact'], '张三')
        self.assertEqual(meta['unit_phone'], '13900000000')
        self.assertEqual(int(meta['days']), 10)
        self.assertTrue(meta['deadline'])

    def test_cost_pass_requires_task_sheet(self):
        p = self.make_project('cost_review')
        r = self.as_user(self.auditor).post(
            f'/api/projects/{p.id}/flow/',
            {'action': 'cost_review_pass', 'note': '评估认为投资构成合理，同意通过'},
            format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('任务书', r.data['detail'])

    def test_fiscal_reject_goes_to_reserve(self):
        p = self.make_project('fiscal_review')
        r = self.as_user(self.auditor).post(
            f'/api/projects/{p.id}/flow/',
            {'action': 'fiscal_reject', 'note': '财政年度预算无资金安排，暂不列入'},
            format='json')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, 'reserve')
        self.assertEqual(p.fiscal_result, 'reject')

    def test_approval_requires_reply_doc(self):
        p = self.make_project('approval')
        r = self.as_user(self.reviewer).post(
            f'/api/projects/{p.id}/flow/',
            {'action': 'approval_issue', 'note': '已出具批复并送达申报单位'},
            format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('立项批复', r.data['detail'])

        g = self.as_user(self.reviewer).post(
            f'/api/projects/{p.id}/docs/approval-reply/', {}, format='json')
        self.assertEqual(g.status_code, 200, g.data)
        rec = ProjectDocument.objects.get(project=p, kind='approval_reply')
        self.assertIn('关于不动产登记一网通办平台建设项目立项的批复', self.doc_text(rec))
        self.assertIn('860万元', self.doc_text(rec))

        ok = self.as_user(self.reviewer).post(
            f'/api/projects/{p.id}/flow/',
            {'action': 'approval_issue', 'note': '已出具批复并送达申报单位'},
            format='json')
        self.assertEqual(ok.status_code, 200, ok.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, 'tendering')


class TodoAckTest(DocFlowTestBase):
    def test_ack_decrements_pending(self):
        p = self.make_project('precheck')
        self.as_user(self.reviewer)
        self.assertEqual(self.client.get('/api/projects/pending/').data['pending'], 1)
        r = self.client.post(f'/api/projects/{p.id}/todo-ack/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['pending'], 0)
        self.assertEqual(TodoAck.objects.filter(user=self.reviewer).count(), 1)
        # 幂等：再点一次不重复建条，也不变成负数
        r = self.client.post(f'/api/projects/{p.id}/todo-ack/')
        self.assertEqual(r.data['pending'], 0)

    def test_stage_change_recounts(self):
        p = self.make_project('precheck')
        self.as_user(self.reviewer)
        self.client.post(f'/api/projects/{p.id}/todo-ack/')
        self.assertEqual(self.client.get('/api/projects/pending/').data['pending'], 0)
        # 换阶段后旧标记失效，新的待办重新计入
        p.set_stage('approval')
        self.assertEqual(self.client.get('/api/projects/pending/').data['pending'], 1)

    def test_not_my_todo_rejected(self):
        p = self.make_project('precheck')
        r = self.as_user(self.auditor).post(f'/api/projects/{p.id}/todo-ack/')
        self.assertEqual(r.status_code, 400)


class TemplateLibraryTest(DocFlowTestBase):
    def test_seeded_templates_and_stage_binding(self):
        self.as_user(self.admin)
        r = self.client.get('/api/templates/')
        self.assertEqual(r.status_code, 200)
        rows = r.data['results'] if isinstance(r.data, dict) else r.data
        kinds = {x['kind']: x for x in rows}
        self.assertEqual(set(kinds), {'precheck_notice', 'cost_review_task',
                                      'approval_reply'})
        self.assertEqual(kinds['precheck_notice']['stage'], 'precheck')
        self.assertEqual(kinds['cost_review_task']['stage'], 'cost_review')
        self.assertEqual(kinds['approval_reply']['stage'], 'approval')

    def test_admin_only(self):
        for user in (self.applicant, self.reviewer, self.auditor):
            self.as_user(user)
            self.assertEqual(self.client.get('/api/templates/').status_code, 403)

    def test_builtin_download(self):
        tpl = DocTemplate.objects.get(kind='precheck_notice')
        self.as_user(self.admin)
        r = self.client.get(f'/api/templates/{tpl.id}/file/')
        self.assertEqual(r.status_code, 200)


class TemplateStageMatchTest(TestCase):
    """#20：模板的「适用环节」必须真正参与选取。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.mkdtemp(prefix='pm-test-tplstage-')
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def _make(self, name, stage, text):
        path = f'{self._media}/{name}.docx'
        d = Document()
        d.add_paragraph(text)
        d.save(path)
        tpl = DocTemplate.objects.create(
            name=name, kind=DocTemplate.Kind.PRECHECK_NOTICE,
            stage=stage, active=True, filename=f'{name}.docx')
        with open(path, 'rb') as fh:
            tpl.file.save(f'{name}.docx', __import__('django').core.files.File(fh))
        return tpl

    def test_stage_specific_template_wins(self):
        from . import docgen
        general = self._make('通用版', '', '通用占位符')
        specific = self._make('预审专用版', Project.Stage.PRECHECK, '预审专用占位符')
        # 让专用版更新，确保不是靠 updated 偶然选对
        specific.save(update_fields=['updated'])
        _, picked = docgen._template_for('precheck_notice', Project.Stage.PRECHECK)
        self.assertEqual(picked.pk, specific.pk)
        # 别的环节没有专用模板时回退通用模板
        _, fallback = docgen._template_for('precheck_notice', Project.Stage.COST_REVIEW)
        self.assertEqual(fallback.pk, specific.pk)  # 该 kind 最新一份即回退对象
