"""流转主流程与硬约束的回归测试。

跑法：.venv/bin/python manage.py test apps.project -v 2
样例材料取自「样例数据」里的可研，走真实的解析与规则预审，不打桩。
"""
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.models import Role, User

from . import docgen, views
from .models import (DocTemplate, PrecheckReport, Project, ProjectDocument,
                     ProjectEvent, ProjectFile, ReviewFile, StageSla)

SAMPLE = Path(settings.PROJECT_ROOT) / '样例数据' / '01_市不动产登记信息平台_可行性研究报告.docx'


class FlowTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.applicant = User.objects.create_user(
            username='u_app', password='Pm@12345678', name='申报员',
            role=User.Role.APPLICANT, unit='财政局')
        cls.applicant2 = User.objects.create_user(
            username='u_app2', password='Pm@12345678', name='别家申报员',
            role=User.Role.APPLICANT, unit='市教育局')
        cls.reviewer = User.objects.create_user(
            username='u_rev', password='Pm@12345678', name='初审员',
            role=User.Role.REVIEWER, unit='数据管理局')
        cls.auditor = User.objects.create_user(
            username='u_aud', password='Pm@12345678', name='审核员',
            role=User.Role.AUDITOR, unit='数据管理局')

    def client_for(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def make_project(self):
        c = self.client_for(self.applicant)
        r = c.post('/api/projects/', {
            'name': '市不动产登记信息平台升级改造项目',
            'unit': '财政局', 'year': 2026, 'category': '自然资源',
            'contact_name': '王经办', 'contact_phone': '13800000000',
            'investment': 860, 'duration_months': 12,
            'summary': '建设不动产登记信息平台，实现登记业务全流程网上办理、'
                       '与税务和住建部门数据共享，覆盖全市 12 个登记大厅。' * 2,
            'necessity': '现有系统建于 2015 年，仅支持窗口办理，日均受理量已超出设计能力 3 倍。',
            'basis': '《关于加快推进不动产登记便利化改革的意见》',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        return Project.objects.get(pk=r.data['id'])

    def upload(self, project):
        if not SAMPLE.exists():
            self.skipTest('样例材料不存在')
        c = self.client_for(self.applicant)
        data = SAMPLE.read_bytes()
        f = SimpleUploadedFile('01_市不动产登记信息平台_可行性研究报告.docx', data)
        r = c.post(f'/api/projects/{project.id}/files/', {'files': [f]},
                   format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['results'][0]['ok'], r.data['results'][0]['message'])
        return r

    def flow(self, user, project, key, **data):
        c = self.client_for(user)
        return c.post(f'/api/projects/{project.id}/flow/',
                      {'action': key, **data}, format='json')


class HappyPathTest(FlowTestBase):
    def test_full_lifecycle(self):
        p = self.make_project()
        self.assertEqual(p.stage, Project.Stage.DRAFT)
        self.upload(p)
        p.refresh_from_db()
        self.assertTrue(p.files.filter(status='ready').exists())

        r = self.flow(self.applicant, p, 'submit', note='首次申报')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.PRECHECK)
        self.assertEqual(p.handler_role, User.Role.REVIEWER)
        self.assertIn('level', p.dup_check or {})
        self.assertEqual(p.dup_check.get('scanned'), 0)  # 测试库没有历史档案

        # 没做规则预审不能初审通过（预审报告是上会的前置条件）
        r = self.flow(self.reviewer, p, 'precheck_pass', note='材料齐全，同意提交评审')
        self.assertEqual(r.status_code, 400)
        self.assertIn('规则预审', r.data['detail'])

        r = self.flow(self.reviewer, p, 'rule_check')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        report = p.reports.filter(level='rule').first()
        self.assertIsNotNone(report)
        self.assertEqual(report.counts['block'], 0,
                         [i['title'] for i in report.items if i['severity'] == 'block'])

        r = self.flow(self.reviewer, p, 'precheck_pass',
                      note='材料齐全，功能与需求基本对应，同意提交专家评审')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.EXPERT_REVIEW)

        # 没上传评审会材料不能出结论
        r = self.flow(self.reviewer, p, 'conclude',
                      votes={'agree': 2, 'modified': 0, 'reject': 1},
                      note='三位专家到会，两人同意通过')
        self.assertEqual(r.status_code, 400)
        self.assertIn('评审会材料', r.data['detail'])

        # 各家意见表与汇总表都传（不等于一份 PDF 拼一起）
        for name, kind in (('专家甲意见表.pdf', 'expert'), ('专家乙意见表.pdf', 'expert'),
                           ('评审会汇总意见.pdf', 'summary')):
            ReviewFile.objects.create(project=p, file=f'review/202611/{name}',
                                      name=name, kind=kind)
        self.assertEqual(p.review_files.count(), 3)
        r = self.flow(self.reviewer, p, 'conclude',
                      votes={'agree': 1, 'modified': 1, 'reject': 1},
                      note='三位专家到会，两人同意（其中一人要求修改后通过）')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        # 修改后通过不是「直接转审核」：先开补正，改完材料才交审核
        self.assertEqual(p.stage, Project.Stage.REVIEW_SUPPLEMENT)
        self.assertEqual(p.review_result, 'modified_pass')
        self.assertEqual((p.review_agree, p.review_modified, p.review_reject),
                         (1, 1, 1))
        notice = p.notices.filter(kind='review').first()
        self.assertIsNotNone(notice)
        self.assertEqual(notice.status, 'open')
        self.assertEqual(notice.round, p.round)
        # 留痕里要能看出结论是怎么算出来的
        self.assertIn('通过 2/3', p.events.filter(action='conclude')
                      .latest('created').comment)

        # 没传新版材料不许提交，传了才转审核
        r = self.flow(self.applicant, p, 'review_resubmit', note='已按意见修改')
        self.assertEqual(r.status_code, 400)
        self.assertIn('修改后的材料', r.data['detail'])
        ProjectFile.objects.filter(project=p).update(round=p.round)
        r = self.flow(self.applicant, p, 'review_resubmit',
                      note='已按评审会意见补充绩效指标与投资明细')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.COST_REVIEW)
        self.assertEqual(p.notices.filter(kind='review').first().status, 'resolved')

        # 费用评审要先出任务书，没出文书不许往下走
        from .models import ProjectDocument
        ProjectDocument.objects.create(
            project=p, kind='cost_review_task', name='费用评审任务书.docx',
            file='generated/test_task.docx')
        # 结论由第三方报告承担：报告没上传，不许记「通过」
        r = self.flow(self.auditor, p, 'cost_review_pass')
        self.assertEqual(r.status_code, 400)
        self.assertIn('评估报告', r.data['detail'])
        ProjectFile.objects.create(
            project=p, doc_type=ProjectFile.DocType.COST_REPORT, round=p.round,
            filename='费用评估报告.pdf', file='project/202611/report.pdf',
            status=ProjectFile.Status.READY)
        r = self.flow(self.auditor, p, 'cost_review_pass')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.FISCAL_REVIEW)
        self.assertIn('费用评估报告', p.audit_note)

        # 财政审核同意 → 立项（不是直接待招标）
        r = self.flow(self.auditor, p, 'fiscal_pass',
                      note='市财政局同意安排资金，列入 2027 年度预算')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.APPROVAL)

        # 立项要先出批复
        ProjectDocument.objects.create(
            project=p, kind='approval_reply', name='立项批复.docx',
            file='generated/test_approval.docx')
        r = self.flow(self.reviewer, p, 'approval_issue',
                      note='已出具立项批复并送达申报单位，批复文号克数发〔2026〕12 号')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.TENDERING)

        self.assertEqual(self.flow(self.reviewer, p, 'start_build', note='已定标').status_code, 200)
        self.assertEqual(self.flow(self.reviewer, p, 'start_trial', note='初验通过').status_code, 200)
        # 试运行由建设单位执行：初审岗不再能代办，且试运行情况与归集情况必填
        r = self.flow(self.reviewer, p, 'apply_acceptance',
                      trial_summary='试运行三个月，系统运行稳定，未发生影响业务的故障',
                      data_collect_scope='已归集业务数据约 12 万条')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('申报单位', r.data['detail'])
        r = self.flow(self.applicant, p, 'apply_acceptance', note='申请验收')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('试运行情况', r.data['detail'])
        r = self.flow(self.applicant, p, 'apply_acceptance',
                      trial_summary='试运行 3 个月稳定，未发生影响业务的故障')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('数据归集情况', r.data['detail'])

        r = self.flow(self.applicant, p, 'apply_acceptance',
                      trial_summary='2026 年 9 月至 11 月试运行 3 个月，系统运行稳定，'
                                    '未发生影响业务的故障，遗留问题 2 项',
                      data_collect_scope='已归集业务数据：数据库 1 个、业务表 23 张，'
                                         '累计约 12 万条，已通过市数据共享平台入库')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.ACCEPTANCE)
        self.assertIn('12 万条', p.data_collect_scope)
        # 待验收不再放开任何扫码件上传（证明是生成的，不是收的）
        self.assertFalse(views._can_upload_proof(p, self.auditor))

        # 没有出具《数据归集证明》不能验收：硬约束，不是提示
        r = self.flow(self.auditor, p, 'accept', note='验收合格，同意结项')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('数据归集证明', r.data['detail'])

        # 证明按模板出具，正文取单位填报的归集情况
        rec = docgen.generate_data_collect(p, {}, self.auditor)
        self.assertEqual(rec.kind, 'data_collect')
        self.assertIn('12 万条', rec.meta['data_collect'])

        # 专家验收意见不再卡结项：只凭证明就能验收，意见结项后备案
        r = self.flow(self.auditor, p, 'accept',
                      note='验收合格，功能清单与合同一致，同意结项')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.ACCEPTED)

        # 结项后才收专家验收意见
        self.assertTrue(views._can_upload_proof(p, self.auditor))

        # 每一步都留痕
        actions = list(p.events.values_list('action', flat=True))
        for key in ('create', 'upload', 'submit', 'rule_check', 'precheck_pass',
                    'conclude', 'cost_review_pass', 'fiscal_pass', 'approval_issue',
                    'start_build', 'start_trial',
                    'apply_acceptance', 'accept'):
            self.assertIn(key, actions)
        self.assertGreaterEqual(ProjectEvent.objects.filter(project=p).count(), 11)


class ConstraintTest(FlowTestBase):
    def setUp(self):
        self.p = self.make_project()
        self.upload(self.p)
        p = self.p
        self.flow(self.applicant, p, 'submit', note='首次申报')
        p.refresh_from_db()

    def test_block_cannot_approve(self):
        """有「必须补正」项时不能初审通过。把项目名称改短制造一条硬性缺项。"""
        p = self.p
        p.name = '房'
        p.save(update_fields=['name'])
        r = self.flow(self.reviewer, p, 'rule_check')
        self.assertEqual(r.status_code, 200, r.data)
        r = self.flow(self.reviewer, p, 'precheck_pass',
                      note='材料齐全，同意提交专家评审')
        self.assertEqual(r.status_code, 400)
        self.assertIn('必须补正', r.data['detail'])

    def test_return_and_resubmit_rounds(self):
        p = self.p
        r = self.flow(self.reviewer, p, 'precheck_return',
                      items=[{'title': '补充投资概算分项明细', 'requirement': '按工程费、其他费、预备费分解'}],
                      note='投资构成不清', deadline='2026-12-01')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.SUPPLEMENT)
        self.assertEqual(p.round, 1)
        notice = p.notices.first()
        self.assertEqual(notice.status, 'open')

        # 审查项 #15：补正不能空手提交，先造一份本轮（round=1）材料
        ProjectFile.objects.create(
            project=p, file='projects/x/resubmit.txt', filename='补正说明.txt',
            doc_type=ProjectFile.DocType.FEASIBILITY, uploaded_by=self.applicant,
            round=1, status=ProjectFile.Status.READY)
        r = self.flow(self.applicant, p, 'resubmit', note='已补充概算分项表，见附件第二章')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.PRECHECK)
        self.assertEqual(p.notices.first().status, 'resolved')

    def test_role_guards(self):
        p = self.p
        # 申报单位不能自己预审通过
        r = self.flow(self.applicant, p, 'precheck_pass',
                      note='我自己通过一下看看行不行')
        self.assertEqual(r.status_code, 400)
        # 别的申报单位看不到、也动不了
        c = self.client_for(self.applicant2)
        self.assertEqual(c.get(f'/api/projects/{p.id}/').status_code, 404)
        # 申报单位也不能录评审会结论（环节没到，且不是他的活）
        r = self.flow(self.applicant, p, 'conclude',
                      votes={'agree': 3, 'modified': 0, 'reject': 0},
                      note='我自己给个结论')
        self.assertEqual(r.status_code, 400)

    def test_oppose_blocks_pass(self):
        """1:2 不过半 → 结论「不通过」并终止，不再推给审核岗否一次。"""
        p = self.p
        self.flow(self.reviewer, p, 'rule_check')
        r = self.flow(self.reviewer, p, 'precheck_pass',
                      note='材料齐全，同意提交专家评审')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        ReviewFile.objects.create(project=p, file='review/202611/scan.pdf',
                                  name='评审意见.pdf', kind='summary')
        r = self.flow(self.reviewer, p, 'conclude',
                      votes={'agree': 1, 'modified': 0, 'reject': 2},
                      note='两位专家认为与已建系统重合，建议整合后再报')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.review_result, Project.ReviewResult.REJECT)
        self.assertEqual(p.stage, Project.Stage.REJECTED)
        # 终止就是终止，不该留一张待办给审核岗
        self.assertFalse(p.notices.filter(kind='review').exists())

    def test_even_votes_rejected(self):
        """偶数票会平票，直接拒绝录入，别让流程卡在半路。"""
        p = self.p
        self.flow(self.reviewer, p, 'rule_check')
        self.flow(self.reviewer, p, 'precheck_pass', note='材料齐全，同意提交专家评审')
        p.refresh_from_db()
        ReviewFile.objects.create(project=p, file='review/202611/scan.pdf',
                                  name='评审意见.pdf', kind='summary')
        for votes in ({'agree': 2, 'modified': 0, 'reject': 2},
                      {'agree': 0, 'modified': 0, 'reject': 0},
                      {'agree': -1, 'modified': 1, 'reject': 1}):
            r = self.flow(self.reviewer, p, 'conclude', votes=votes,
                          note='票数填错了，系统应该拦住我')
            self.assertEqual(r.status_code, 400, votes)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.EXPERT_REVIEW)

    def test_edit_locked_after_submit(self):
        c = self.client_for(self.applicant)
        r = c.patch(f'/api/projects/{self.p.id}/', {'investment': 100}, format='json')
        self.assertEqual(r.status_code, 403)
        r = c.delete(f'/api/projects/{self.p.id}/')
        self.assertEqual(r.status_code, 400)

    def test_todos(self):
        c = self.client_for(self.reviewer)
        r = c.get('/api/projects/todos/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['items'][0]['actions'][0]['key'], 'rule_check')

        c = self.client_for(self.applicant)
        r = c.get('/api/projects/todos/')
        self.assertEqual(r.data['count'], 0)

    def test_stats_and_pending(self):
        """统计与侧栏角标。stats 曾因 select_related + only 冲突直接 500，
        首页 Promise.all 里它一挂，待办列表也跟着空白，必须有回归。

        口径（2026-11-24 用户拍板）：台账与统计只算自己经办过的件 ——
        初审岗还没动手时是 0，办过一次才是 1。
        """
        c = self.client_for(self.reviewer)
        r = c.get('/api/projects/stats/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['total'], 0)

        self.flow(self.reviewer, self.p, 'rule_check')
        r = c.get('/api/projects/stats/')
        self.assertEqual(r.data['total'], 1)
        self.assertEqual(len(r.data['by_stage']), len(Project.Stage.choices))
        self.assertIn('investment_total', r.data)

        r = c.get('/api/projects/pending/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['pending'], 1)

    def test_reviewer_ledger_only_counts_own_work(self):
        """「谁的项目就只看谁的」：别人的件不进审核岗的已办项目，
        经手过才进来；但明细照样能开（待办里点进来要看得见）。"""
        c = self.client_for(self.reviewer)
        self.assertEqual(c.get('/api/projects/').data['count'], 0)
        self.flow(self.reviewer, self.p, 'rule_check')
        self.assertEqual(c.get('/api/projects/').data['count'], 1)
        self.assertEqual(c.get(f'/api/projects/{self.p.id}/').status_code, 200)

    def test_report_and_export(self):
        """统计与导出：字段齐全、CSV 带 BOM（Excel 双击不乱码）。"""
        # 用申报单位身份看统计：它看到的是自己的件，与「经办口径」无关
        c = self.client_for(self.applicant)
        r = c.get('/api/projects/report/')
        self.assertEqual(r.status_code, 200)
        for key in ('summary', 'stage_days', 'funnel', 'return_rate',
                    'review', 'by_unit', 'by_category', 'by_year'):
            self.assertIn(key, r.data)
        self.assertEqual(r.data['summary']['total'], 1)
        # 阶段耗时表要覆盖全部阶段，缺的阶段界面会少一行
        self.assertEqual(len(r.data['stage_days']), len(Project.Stage.choices))

        for kind in ('projects', 'events'):
            r = c.get(f'/api/projects/export/?kind={kind}')
            self.assertEqual(r.status_code, 200, kind)
            self.assertIn('text/csv', r['Content-Type'])
            self.assertTrue(r.content.startswith(b'\xef\xbb\xbf'), 'CSV 缺 BOM')

        r = c.get('/api/projects/export/?kind=unknown')
        self.assertEqual(r.status_code, 400)

    def test_rerun_precheck_replaces_report(self):
        """同一轮重复点预审，只保留一份报告，不叠垃圾。"""
        p = self.p
        for _ in range(3):
            r = self.flow(self.reviewer, p, 'rule_check')
            self.assertEqual(r.status_code, 200, r.data)
        n = PrecheckReport.objects.filter(project=p, level='rule',
                                          round=p.round).count()
        self.assertEqual(n, 1, f'同轮规则预审应只剩 1 份报告，实际 {n} 份')


class ReviewFileFlowTest(FlowTestBase):
    """接口级走一遍：多份意见表上传 → 录票 → 修改后通过进补正 → 改完交审核。

    单元层已经覆盖了票数与结论，但「材料走 multipart 上传、票数走 JSON
    流转、补正走另一轮材料」是三段不同的请求，中间字段没接上就会一头通一头不通。
    """

    def setUp(self):
        self.p = self.make_project()
        self.upload(self.p)
        self.flow(self.applicant, self.p, 'submit', note='首次申报')
        self.flow(self.reviewer, self.p, 'rule_check')
        r = self.flow(self.reviewer, self.p, 'precheck_pass',
                      note='材料齐全，同意提交专家评审')
        self.assertEqual(r.status_code, 200, r.data)
        self.p.refresh_from_db()

    def post_files(self, files, kind, user=None):
        c = self.client_for(user or self.reviewer)
        return c.post(f'/api/projects/{self.p.id}/review-files/',
                      {'files': files, 'kind': kind}, format='multipart')

    def pdf(self, name):
        return SimpleUploadedFile(name, b'%PDF-1.4 fake', content_type='application/pdf')

    def test_upload_each_expert_sheet(self):
        """一场会收上来好几张表，必须能一份一份存，而不是拼成一个 PDF。"""
        r = self.post_files([self.pdf('专家甲.pdf'), self.pdf('专家乙.pdf')], 'expert')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['saved'], 2)

        r = self.post_files([self.pdf('汇总表.pdf')], 'summary')
        self.assertEqual(r.status_code, 200, r.data)

        self.p.refresh_from_db()
        self.assertEqual(self.p.review_files.count(), 3)
        self.assertEqual(self.p.review_files.filter(kind='expert').count(), 2)
        detail = self.client_for(self.reviewer).get(f'/api/projects/{self.p.id}/').data
        self.assertEqual(len(detail['review_files']), 3)
        kinds = {f['kind'] for f in detail['review_files']}
        self.assertEqual(kinds, {'expert', 'summary'})

    def test_delete_file(self):
        r = self.post_files([self.pdf('传错的.pdf')], 'expert')
        self.assertEqual(r.status_code, 200, r.data)
        fid = self.p.review_files.first().id
        c = self.client_for(self.reviewer)
        r = c.delete(f'/api/projects/{self.p.id}/review-files/{fid}/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.p.review_files.count(), 0)

    def test_upload_rejects_wrong_type(self):
        r = self.post_files([SimpleUploadedFile(
            '名单.docx', b'xx', content_type='application/octet-stream')], 'expert')
        self.assertEqual(r.status_code, 400)
        self.assertIn('PDF', r.data['detail'])

    def test_upload_only_reviewer(self):
        r = self.post_files([self.pdf('专家甲.pdf')], 'expert', user=self.applicant)
        self.assertIn(r.status_code, (403, 404))

    def test_conclude_modified_goes_to_supplement(self):
        self.post_files([self.pdf('专家甲.pdf'), self.pdf('汇总表.pdf')], 'expert')
        r = self.flow(self.reviewer, self.p, 'conclude',
                      votes={'agree': 1, 'modified': 1, 'reject': 1},
                      note='三人到会，两人同意，其中一人要求修改后通过')
        self.assertEqual(r.status_code, 200, r.data)
        self.p.refresh_from_db()
        self.assertEqual(self.p.stage, Project.Stage.REVIEW_SUPPLEMENT)
        self.assertEqual(self.p.review_result, Project.ReviewResult.MODIFIED)

        detail = self.client_for(self.applicant).get(f'/api/projects/{self.p.id}/').data
        self.assertEqual(detail['votes']['total'], 3)
        self.assertEqual(detail['votes']['yes'], 2)
        self.assertTrue(detail['votes']['recorded'])
        # 结论存住了，申报单位看到的是「待补正」而不是空白
        self.assertTrue(any(n['kind'] == 'review' and n['status'] == 'open'
                            for n in detail['notices']))
        self.assertIn('review_resubmit', [a['key'] for a in detail['actions']])

    def test_review_supplement_upload_only(self):
        """评审后补正：能换材料，不能顺手把投资改小 —— 投资一变评审结论就作废。"""
        self.p.stage = Project.Stage.REVIEW_SUPPLEMENT
        self.p.save(update_fields=['stage'])
        c = self.client_for(self.applicant)
        r = c.patch(f'/api/projects/{self.p.id}/', {'investment': 999}, format='json')
        self.assertEqual(r.status_code, 403)

        f = SimpleUploadedFile(SAMPLE.name, SAMPLE.read_bytes())
        r = c.post(f'/api/projects/{self.p.id}/files/', {'files': [f]},
                   format='multipart')
        self.assertEqual(r.status_code, 200, r.data)


class SheetUploadTest(FlowTestBase):
    """投资概算、报价单这类材料多是 Excel，要能传。

    但表格不是可研正文：不切章节、不进查重与功能抽取，只存文字供预览检索，
    字数门槛也不能按「200 字」卡 —— 一张预算表往往只有几十字加一堆数字。
    """

    def _xlsx(self, name='投资概算明细表.xlsx'):
        import io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = '概算'
        ws.append(['序号', '科目', '金额（万元）'])
        ws.append([1, '软件开发费', 120.5])
        ws.append([2, '硬件购置费', 380])
        buf = io.BytesIO()
        wb.save(buf)
        return SimpleUploadedFile(name, buf.getvalue())

    def test_xlsx_is_stored_without_section_split(self):
        p = self.make_project()
        c = self.client_for(self.applicant)
        r = c.post(f'/api/projects/{p.id}/files/', {'files': [self._xlsx()]},
                   format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['results'][0]['ok'], r.data['results'][0]['message'])

        rec = p.files.get(filename='投资概算明细表.xlsx')
        self.assertEqual(rec.status, ProjectFile.Status.READY)
        self.assertEqual(rec.doc_type, ProjectFile.DocType.BUDGET)
        self.assertIn('软件开发费', rec.text)      # 单元格文字读得出来
        self.assertEqual(rec.sections, [])          # 不切章节
        self.assertEqual(rec.functions, [])         # 不进功能抽取
        self.assertEqual(rec.grams, {})             # 不进查重指纹

    def test_xls_old_format_asks_for_xlsx(self):
        p = self.make_project()
        c = self.client_for(self.applicant)
        f = SimpleUploadedFile('概算表.xls', b'not a real xls')
        r = c.post(f'/api/projects/{p.id}/files/', {'files': [f]}, format='multipart')
        res = r.data['results'][0]
        self.assertFalse(res['ok'])
        self.assertIn('.xlsx', res['message'])


class CostReviewRoundsTest(FlowTestBase):
    """费用评审可以有多轮：退回 → 单位补异议证据 → 回费用评审重评。

    这条口径的关键是「不回到预审」：走了预审补正就要重跑预审、重开评审会，
    等于让同一批人把同一个结论再表决一次。
    """

    def setUp(self):
        self.p = self.make_project()
        self.upload(self.p)
        p = self.p
        self.flow(self.applicant, p, 'submit', note='首次申报')
        self.flow(self.reviewer, p, 'rule_check')
        r = self.flow(self.reviewer, p, 'precheck_pass',
                      note='材料齐全，功能与需求基本对应，同意提交专家评审')
        self.assertEqual(r.status_code, 200, r.data)
        for name, kind in (('专家甲意见表.pdf', 'expert'), ('评审会汇总.pdf', 'summary')):
            ReviewFile.objects.create(project=p, file=f'review/202611/{name}',
                                      name=name, kind=kind)
        r = self.flow(self.reviewer, p, 'conclude',
                      votes={'agree': 3, 'modified': 0, 'reject': 0},
                      note='三位专家到会，一致同意通过')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.COST_REVIEW)

    def test_return_then_evidence_then_second_round(self):
        p = self.p
        r = self.flow(self.auditor, p, 'cost_review_return',
                      items=[{'title': '投资概算偏高', 'requirement': '按市场询价重新核定软件费用'}],
                      note='概算中软件开发费高于同类项目，退回补充询价依据',
                      deadline='2026-12-10')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        # 进入「征求反馈」，不是预审补正
        self.assertEqual(p.stage, Project.Stage.COST_SUPPLEMENT)
        self.assertEqual(p.handler_role, User.Role.APPLICANT)
        notice = p.notices.filter(kind='audit').first()
        self.assertEqual(notice.status, 'open')

        # 既没材料也没说明也允许提交，这里走有材料的路径看主流程
        # 补一份本轮的异议证据（类型 evidence）
        ProjectFile.objects.create(
            project=p, file='project/202611/询价单.pdf', filename='软件询价单.pdf',
            doc_type=ProjectFile.DocType.EVIDENCE, round=p.round,
            version=1, is_current=True, status=ProjectFile.Status.READY)

        r = self.flow(self.applicant, p, 'cost_resubmit',
                      note='三家供应商询价均在概算范围内，见随附询价单')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        # 回费用评审重评，不经过预审
        self.assertEqual(p.stage, Project.Stage.COST_REVIEW)
        self.assertEqual(p.handler_role, User.Role.AUDITOR)
        self.assertEqual(p.notices.filter(kind='audit').first().status, 'resolved')

        # 详情页能按轮次还原：第 1 轮退回、第 2 轮提交证据
        c = self.client_for(self.auditor)
        d = c.get(f'/api/projects/{p.id}/').data
        rounds = [(x['round'], x['action']) for x in d['cost_flow']]
        self.assertEqual(rounds, [(1, 'cost_review_return'), (2, 'cost_resubmit')])

        # 第二轮再退，轮次继续往上加
        r = self.flow(self.auditor, p, 'cost_review_return',
                      items=[{'title': '机房托管费需单列', 'requirement': '补一份托管单价说明'}],
                      note='还有托管费未提供计价依据，再退一次')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.COST_SUPPLEMENT)
        d = c.get(f'/api/projects/{p.id}/').data
        self.assertEqual(d['cost_flow'][-1]['round'], 2)

    def test_resubmit_without_objection_needs_no_note(self):
        """单位对评估结论没异议、只按反馈事项补材料时，不必凑满十个字。

        退回多半是要补计价依据，单位照办本来就是「无异议」。原来这步
        把补正说明设成必填且不少于 10 字，实际只会逼出一堆
        「已按要求补充材料，无异议」的废话，留痕价值反而更低。
        """
        p = self.p
        r = self.flow(self.auditor, p, 'cost_review_return',
                      items=[{'title': '软件费用需询价', 'requirement': '补三份供应商报价单'}],
                      note='软件开发费缺少询价依据，退回补充', deadline='2026-12-10')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()          # 退回时 round 已 +1，材料要挂在新轮次上
        ProjectFile.objects.create(
            project=p, file='project/202611/报价单.pdf', filename='三家报价单.pdf',
            doc_type=ProjectFile.DocType.EVIDENCE, round=p.round,
            version=1, is_current=True, status=ProjectFile.Status.READY)

        # 填了但太短 → 仍然拒，避免「无异议」三个字糊过去
        r = self.flow(self.applicant, p, 'cost_resubmit', note='无异议')
        self.assertEqual(r.status_code, 400)
        self.assertIn('10', r.data['detail'])

        # 不填 → 放行，留痕写成「无异议，已按反馈事项补充材料」
        r = self.flow(self.applicant, p, 'cost_resubmit')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.COST_REVIEW)
        notice = p.notices.filter(kind='audit').order_by('-id').first()
        self.assertIn('无异议', notice.reply)
        self.assertIn('三家报价单.pdf', notice.reply)

    def test_resubmit_note_only_without_files(self):
        """退回只要一句解释、手上确实没新材料的，写清说明也能回评审。

        原来卡「必须传文件」，实际会逼着单位把老材料换个名字再传一遍，
        留痕反而更假。
        """
        p = self.p
        r = self.flow(self.auditor, p, 'cost_review_return',
                      items=[{'title': '建设内容表述笼统', 'requirement': '补充逐项说明'}],
                      note='建设内容表述过于笼统，退回补充说明', deadline='2026-12-10')
        self.assertEqual(r.status_code, 200, r.data)

        r = self.flow(self.applicant, p, 'cost_resubmit',
                      note='建设内容未变，逐项说明见原可研第三章，本次无需新增材料')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.COST_REVIEW)
        notice = p.notices.filter(kind='audit').order_by('-id').first()
        self.assertIn('无需新增材料', notice.reply)

    def test_cost_return_action_is_called_feedback(self):
        """措辞用「征求反馈」：不是材料被否，而是请单位确认评估意见。

        叫「退回补正」容易让单位以为材料不合格、要重做一遍。
        """
        acts = self.client_for(self.auditor).get(f'/api/projects/{self.p.id}/').data['actions']
        act = next(x for x in acts if x['key'] == 'cost_review_return')
        self.assertEqual(act['label'], '征求反馈')
        labels = [f['label'] for f in act['form']]
        self.assertIn('需要单位反馈的事项', labels)

    def test_resubmit_with_nothing_at_all(self):
        """没有异议、也没有材料要补的，空手提交也得放行。

        这类情形本来就没什么可填：反馈事项核对后认为原样即可、评估结论也认，
        硬凑十个字只会得到一句套话，还让办理人以为「必须上传材料」。改由系统
        写清「未上传材料、未填写说明」，审核岗按现有材料处理，该维持维持、
        该再退再退。
        """
        p = self.p
        r = self.flow(self.auditor, p, 'cost_review_return',
                      items=[{'title': '设备清单需核对', 'requirement': '与批复清单核对一致性'}],
                      note='设备清单与批复略有出入，退回核对', deadline='2026-12-10')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()

        # 页面靠这两个字段把「留空也行」讲清楚，别被顺手删掉
        act = next(x for x in self.client_for(self.applicant)
                   .get(f'/api/projects/{p.id}/').data['actions']
                   if x['key'] == 'cost_resubmit')
        fld = act['form'][0]
        self.assertFalse(fld['required'])
        self.assertIn('留空', fld['tip'])
        self.assertIn('留空', fld['placeholder'])

        # 不传材料、不写说明，直接提交
        r = self.flow(self.applicant, p, 'cost_resubmit')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.COST_REVIEW)
        self.assertEqual(p.handler_role, User.Role.AUDITOR)
        notice = p.notices.filter(kind='audit').order_by('-id').first()
        self.assertEqual(notice.status, 'resolved')
        self.assertIn('无异议', notice.reply)
        self.assertIn('无需补充材料', notice.reply)
        self.assertIn('未上传材料', notice.reply)


class StageSlaTest(FlowTestBase):
    """环节时限：管理员可调，办理人可见，超期只提示不拦流程。"""

    def setUp(self):
        from .models import StageSla

        StageSla.objects.all().delete()
        self.admin = User.objects.create_user(
            username='u_adm', password='Pm@12345678', name='管理员',
            role=User.Role.ADMIN)

    def test_read_all_write_admin_only(self):
        c = self.client_for(self.reviewer)
        r = c.get('/api/flow-sla/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(x['stage'] == 'precheck' for x in r.data['items']))

        r = c.put('/api/flow-sla/', {'items': [{'stage': 'precheck', 'days': 3}]},
                  format='json')
        self.assertEqual(r.status_code, 403)

        c = self.client_for(self.admin)
        r = c.put('/api/flow-sla/',
                  {'items': [{'stage': 'precheck', 'days': 3},
                             {'stage': 'cost_review', 'days': 12}]},
                  format='json')
        self.assertEqual(r.status_code, 200, r.data)

        r = c.get('/api/flow-sla/')
        days = {x['stage']: x['days'] for x in r.data['items']}
        self.assertEqual(days['precheck'], 3)
        self.assertEqual(days['cost_review'], 12)

    def test_project_reports_overdue(self):
        from datetime import timedelta

        from django.utils import timezone

        p = self.make_project()
        p.stage = Project.Stage.PRECHECK
        p.stage_at = timezone.now() - timedelta(days=9)
        p.save(update_fields=['stage', 'stage_at'])
        d = self.client_for(self.reviewer).get(f'/api/projects/{p.id}/').data
        sla = d['sla']
        self.assertEqual(sla['days'], 5)      # 默认值兜底
        self.assertEqual(sla['used'], 9)      # 与 days_in_stage 同一口径
        self.assertEqual(sla['overdue'], 4)
        self.assertEqual(sla['remain'], 0)


class DupCheckVisibilityTest(FlowTestBase):
    """重复建设对照：申报单位只看结论，不下发别家项目名与原文片段。"""

    def test_applicant_sees_conclusion_only(self):
        p = self.make_project()
        self.upload(p)
        # 草稿隔离后，初审岗看不到 DRAFT；先提交进入预审中再审判重下发
        self.assertEqual(self.flow(self.applicant, p, 'submit').status_code, 200)
        Project.objects.filter(pk=p.pk).update(dup_check={
            'version': 2, 'level': 'attention', 'level_label': '有需说明项',
            'summary': '与 1 个历史项目功能重合', 'scanned': 1, 'compared': 1,
            'checked_at': '2026-11-21',
            'ranking': [{'project_name': '别家单位的项目', 'unit': '别家单位',
                         'snippets': [{'text': '历史原文'}]}],
        })
        d = self.client_for(self.applicant).get(f'/api/projects/{p.id}/').data
        self.assertNotIn('ranking', d['dup_check'])
        self.assertIn('hidden_note', d['dup_check'])
        self.assertEqual(d['dup_check']['level'], 'attention')

        d = self.client_for(self.reviewer).get(f'/api/projects/{p.id}/').data
        self.assertEqual(d['dup_check']['ranking'][0]['project_name'], '别家单位的项目')


class CostReportTest(FlowTestBase):
    """费用评审的结论由第三方《费用评估报告》承担。

    费用评审是财评机构做的，结论、金额、理由都写在报告里。原来还让审核岗
    另填一段「费用评审意见」，实际只能把报告结论抄一遍。现在报告是这一步的
    成果物：没上传不许记「通过」，上传了就不用再写意见。
    """

    def setUp(self):
        from .models import ProjectDocument
        self.p = self.make_project()
        self.upload(self.p)
        p = self.p
        self.flow(self.applicant, p, 'submit', note='首次申报')
        self.flow(self.reviewer, p, 'rule_check')
        self.flow(self.reviewer, p, 'precheck_pass',
                  note='材料齐全，功能与需求基本对应，同意提交专家评审')
        for name, kind in (('专家甲意见表.pdf', 'expert'), ('评审会汇总.pdf', 'summary')):
            ReviewFile.objects.create(project=p, file=f'review/202611/{name}',
                                      name=name, kind=kind)
        r = self.flow(self.reviewer, p, 'conclude',
                      votes={'agree': 3, 'modified': 0, 'reject': 0},
                      note='三位专家到会，一致同意通过')
        self.assertEqual(r.status_code, 200, r.data)
        ProjectDocument.objects.create(
            project=p, kind='cost_review_task', name='费用评审任务书.docx',
            file='generated/test_task.docx')
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.COST_REVIEW)

    def _report(self, name='费用评估报告.txt'):
        # 故意不复用可研样例：同一份内容在本项目已经传过，会被 sha256 去重拦下。
        # 报告门槛是 0 字（扫描件也收），这里给一段长文即可断言解析结果
        body = ('受市数据管理局委托，我中心对该项目投资概算进行了评审。'
                '经核对建设内容、工程量与市场价格，核定总投资控制在 860 万元以内。') * 6
        return SimpleUploadedFile(name, body.encode('utf-8'))

    def test_action_asks_amount_not_opinion(self):
        """通过动作不收意见，只收一个核定投资 —— 批复金额要从这里出。"""
        d = self.client_for(self.auditor).get(f'/api/projects/{self.p.id}/').data
        act = [a for a in d['actions'] if a['key'] == 'cost_review_pass']
        self.assertEqual(len(act), 1)
        self.assertEqual([f['key'] for f in act[0]['form']], ['audit_amount'])
        self.assertEqual(act[0]['form'][0]['type'], 'number')

    def test_pass_blocked_without_report(self):
        r = self.flow(self.auditor, self.p, 'cost_review_pass')
        self.assertEqual(r.status_code, 400)
        self.assertIn('评估报告', r.data['detail'])
        self.p.refresh_from_db()
        self.assertEqual(self.p.stage, Project.Stage.COST_REVIEW)

    def test_pass_after_report(self):
        self.client_for(self.auditor).post(
            f'/api/projects/{self.p.id}/files/', {'files': [self._report()]},
            format='multipart')
        r = self.flow(self.auditor, self.p, 'cost_review_pass')
        self.assertEqual(r.status_code, 200, r.data)
        self.p.refresh_from_db()
        self.assertEqual(self.p.stage, Project.Stage.FISCAL_REVIEW)
        self.assertIn('费用评估报告', self.p.audit_note)
        self.assertIn('通过', self.p.audit_note)

    def test_auditor_upload_forces_report_type(self):
        """审核岗在本环节上传，类型由后端定死 —— 传什么都记为评估报告，
        不可能借这个口子把可研、概算换掉。"""
        r = self.client_for(self.auditor).post(
            f'/api/projects/{self.p.id}/files/',
            {'files': [self._report()], 'doc_type': 'feasibility'},
            format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        rec = ProjectFile.objects.get(project=self.p, doc_type='cost_report')
        self.assertEqual(rec.filename, '费用评估报告.txt')

    def test_applicant_cannot_upload_in_cost_review(self):
        r = self.client_for(self.applicant).post(
            f'/api/projects/{self.p.id}/files/', {'files': [self._report()]},
            format='multipart')
        self.assertEqual(r.status_code, 403)

    def test_report_skips_dup_pool_and_backfill(self):
        """报告不进查重池、不回填项目字段：它与可研本来就大段重合，
        进池只会造出一堆假重复；报告里的金额也不是本项目的投资口径。"""
        self.client_for(self.auditor).post(
            f'/api/projects/{self.p.id}/files/', {'files': [self._report()]},
            format='multipart')
        rec = ProjectFile.objects.get(project=self.p, doc_type='cost_report')
        self.assertEqual(rec.grams, {})
        self.assertEqual(rec.sections, [])
        self.assertEqual(rec.functions, [])
        self.assertGreater(rec.char_count, 200)
        self.assertEqual(rec.status, ProjectFile.Status.READY)


class ProjectFileStageTest(FlowTestBase):
    """材料要记住「上传时在哪个环节」：详情页「项目资料」按环节分文件夹靠它。

    没有这个字段只能按轮次猜，而一轮里可能跨「待补正」与「费用评审」两个环节，
    猜出来的文件夹名对不上。
    """

    def test_upload_marks_current_stage(self):
        p = self.make_project()
        c = self.client_for(self.applicant)
        f = SimpleUploadedFile('投资概算.txt', '总投资 860 万元。'.encode('utf-8'))
        r = c.post(f'/api/projects/{p.id}/files/', {'files': [f]}, format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        rec = p.files.get(filename='投资概算.txt')
        self.assertEqual(rec.stage, Project.Stage.DRAFT)

        d = c.get(f'/api/projects/{p.id}/').data
        item = [x for x in d['files'] if x['id'] == rec.id][0]
        self.assertEqual(item['stage'], Project.Stage.DRAFT)
        self.assertEqual(item['stage_label'], '申报材料')   # 申报那一摞不叫「申报中材料」

    def test_document_stage_label(self):
        from .models import ProjectDocument
        p = self.make_project()
        ProjectDocument.objects.create(
            project=p, kind=DocTemplate.Kind.PRECHECK_NOTICE, name='预审修正说明.docx',
            file='generated/x.docx', stage=Project.Stage.PRECHECK)
        d = self.client_for(self.applicant).get(f'/api/projects/{p.id}/').data
        self.assertEqual(d['documents'][0]['stage_label'], '预审中')


class FiscalFundSourceTest(FlowTestBase):
    """财政审核不再让人选资金来源：按管理办法一律市本级财政预算。"""

    def setUp(self):
        self.p = self.make_project()
        self.p.stage = Project.Stage.FISCAL_REVIEW
        self.p.save(update_fields=['stage'])

    def test_form_has_no_fund_source(self):
        """资金来源仍是固定值，不进表单；只多一个「财政核定投资」。"""
        d = self.client_for(self.auditor).get(f'/api/projects/{self.p.id}/').data
        act = [a for a in d['actions'] if a['key'] == 'fiscal_pass'][0]
        self.assertEqual([f['key'] for f in act['form']], ['note', 'budget_amount'])

    def test_pass_records_fixed_fund_source(self):
        r = self.flow(self.auditor, self.p, 'fiscal_pass',
                      note='已列入 2026 年市本级财政预算，资金安排已落实')
        self.assertEqual(r.status_code, 200, r.data)
        ev = ProjectEvent.objects.filter(project=self.p, action='fiscal_pass').last()
        self.assertEqual(ev.payload.get('fund_source'), '市本级财政预算')


class DraftInboxTest(FlowTestBase):
    """在办箱：填到一半存下来，件从待办移进在办，提交后清掉。

    办理人只该看到自己手上的两个箱子 —— 别人的、全局的在办数不进这里，
    这也是本次改动的口径：待办与在办都按「承办人是我」算。
    """

    def test_draft_moves_item_between_boxes(self):
        p = self.make_project()
        self.upload(p)
        c = self.client_for(self.applicant)
        self.assertEqual(self.flow(self.applicant, p, 'submit').status_code, 200)
        p.refresh_from_db()

        # 刚提交：初审岗的待办箱里有它，在办箱空
        r = self.client_for(self.reviewer).get('/api/projects/inbox/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['todos']['count'], 1)
        self.assertEqual(r.data['doing']['count'], 0)

        # 填一半保存：进在办箱，不再是「还没动手」的待办
        r = self.client_for(self.reviewer).post(
            f'/api/projects/{p.id}/draft/',
            {'action_key': 'precheck_return',
             'payload': {'note': '投资概算缺分项，先存一半',
                         'items': [{'title': '补充投资概算分项明细', 'requirement': ''}],
                         'junk': 1}},
            format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['draft']['payload']['note'], '投资概算缺分项，先存一半')
        # 不属于该动作的键被丢掉，免得回填时冒出莫名其妙的内容
        self.assertNotIn('junk', r.data['draft']['payload'])

        r = self.client_for(self.reviewer).get('/api/projects/inbox/')
        self.assertEqual(r.data['todos']['count'], 0)
        self.assertEqual(r.data['doing']['count'], 1)
        item = r.data['doing']['items'][0]
        self.assertEqual(item['draft']['action_key'], 'precheck_return')
        self.assertEqual(item['draft']['action_label'], '退回补正')

        # 侧栏角标与箱子同源：待办 0、在办 1，两个数字不能各算一套
        r = self.client_for(self.reviewer).get('/api/projects/pending/')
        self.assertEqual(r.data['pending'], 0)
        self.assertEqual(r.data['doing'], 1)

        # 详情页把草稿带出来，前端据此回填
        d = self.client_for(self.reviewer).get(f'/api/projects/{p.id}/').data
        self.assertEqual(d['my_draft']['payload']['note'], '投资概算缺分项，先存一半')

        # 草稿只属于保存人：别的岗位看不到，也进不了他的箱子
        r = self.client_for(self.auditor).get('/api/projects/inbox/')
        self.assertEqual(r.data['doing']['count'], 0)
        self.assertEqual(self.client_for(self.auditor)
                         .get(f'/api/projects/{p.id}/').data['my_draft'], None)

        # 提交动作走完，草稿自动清掉：件已经往下走了，留着会挂在在办箱里
        p.refresh_from_db()
        self.assertEqual(self.flow(
            self.reviewer, p, 'precheck_return',
            items=[{'title': '补充投资概算分项明细', 'requirement': ''}],
            note='材料缺分项，退回修改').status_code, 200)
        self.assertEqual(self.client_for(self.reviewer)
                         .get(f'/api/projects/{p.id}/').data['my_draft'], None)

    def test_draft_rejects_action_not_mine(self):
        p = self.make_project()
        self.upload(p)
        self.flow(self.applicant, p, 'submit')
        p.refresh_from_db()
        # 申报单位不能存初审岗的动作：存了也提交不了，等于给自己摆个假待办
        r = self.client_for(self.applicant).post(
            f'/api/projects/{p.id}/draft/',
            {'action_key': 'precheck_return',
             'payload': {'items': [{'title': '越权草稿'}]}},
            format='json')
        self.assertEqual(r.status_code, 400, r.data)

    def test_applicant_draft_project_stays_in_todos(self):
        """新建的申报单一直在待办箱，点开也不挪走（2026-11-24 口径）。

        申报中不是「办到一半的审批件」，而是「还没交出去的申报单」。
        新建后会直接落详情页填材料，若点开即入在办箱，人回待办工作台
        找不到自己刚建的单，只能转头去已办项目里翻 —— 入口和件都在错的地方。
        """
        p = self.make_project()
        r = self.client_for(self.applicant).get('/api/projects/inbox/')
        self.assertEqual(r.data['todos']['count'], 1)
        self.assertEqual(r.data['doing']['count'], 0)
        self.assertEqual(r.data['todos']['items'][0]['stage'], Project.Stage.DRAFT)

        # 点开详情：仍在待办箱，侧栏「没动手」角标也不减
        self.client_for(self.applicant).post(f'/api/projects/{p.id}/todo-ack/')
        r = self.client_for(self.applicant).get('/api/projects/inbox/')
        self.assertEqual(r.data['todos']['count'], 1)
        self.assertEqual(r.data['doing']['count'], 0)
        self.assertEqual(r.data['pending'], 1)


class CostReviewHoldTest(FlowTestBase):
    """费用评审的「暂存」：件送出去了、报告还没回来，件要留在在办箱。

    没有这一步，7 个自然日的评审期只能二选一 —— 要么件一直挂在待办里被催，
    要么被逼着提前转走。暂存不改阶段、不进留痕，只是把件从待办挪进在办。
    """

    def setUp(self):
        self.p = self.make_project()
        self.upload(self.p)
        p = self.p
        self.flow(self.applicant, p, 'submit', note='首次申报')
        self.flow(self.reviewer, p, 'rule_check')
        r = self.flow(self.reviewer, p, 'precheck_pass',
                      note='材料齐全，功能与需求基本对应，同意提交专家评审')
        self.assertEqual(r.status_code, 200, r.data)
        ReviewFile.objects.create(project=p, file='review/202611/汇总.pdf',
                                  name='评审会汇总.pdf', kind='summary')
        r = self.flow(self.reviewer, p, 'conclude',
                      votes={'agree': 3, 'modified': 0, 'reject': 0},
                      note='三位专家到会，一致同意通过')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.COST_REVIEW)

    def test_hold_requires_task_doc(self):
        """没生成任务书就不许暂存：件没送出去，谈不上「在等报告」。"""
        r = self.client_for(self.auditor).post(
            f'/api/projects/{self.p.id}/draft/',
            {'action_key': 'hold', 'payload': {'note': '先存一下'}}, format='json')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('费用评审任务书', r.data['detail'])

    def test_hold_moves_item_to_doing_and_clears_on_pass(self):
        from .models import ProjectDocument
        p = self.p
        ProjectDocument.objects.create(
            project=p, kind='cost_review_task', name='费用评审任务书.docx',
            file='generated/test_task.docx', meta={'days': 7, 'deadline': '2026-12-01'})

        c = self.client_for(self.auditor)
        r = c.post(f'/api/projects/{p.id}/draft/', {
            'action_key': 'hold',
            'payload': {'note': '已送达财评机构，等评估报告',
                        'entrust_unit': '市财政投资评审中心',
                        'entrust_contact': '赵审核', 'days': 7,
                        'deadline': '2026-12-01', 'junk': 'x'},
        }, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        # 在办箱要看得懂这一条是什么，不能显示内部键 hold
        self.assertEqual(r.data['draft']['action_label'], '保存，等第三方评估报告')
        self.assertNotIn('junk', r.data['draft']['payload'])

        r = c.get('/api/projects/inbox/')
        self.assertEqual(r.data['todos']['count'], 0)
        self.assertEqual(r.data['doing']['count'], 1)
        self.assertEqual(r.data['doing']['items'][0]['draft']['action_key'], 'hold')
        r = c.get('/api/projects/pending/')
        self.assertEqual((r.data['pending'], r.data['doing']), (0, 1))

        # 详情页带着暂存内容回来：任务书参数能补回表单
        d = c.get(f'/api/projects/{p.id}/').data
        self.assertEqual(d['my_draft']['action_key'], 'hold')
        self.assertEqual(d['my_draft']['payload']['entrust_unit'], '市财政投资评审中心')
        self.assertEqual(d['my_draft']['action_label'], '保存，等第三方评估报告')

        # 通过就往下走，暂存随之清掉 —— 否则在办箱会挂一件已经办完的件
        ProjectFile.objects.create(
            project=p, file='project/202611/评估报告.pdf', filename='费用评估报告.pdf',
            doc_type=ProjectFile.DocType.COST_REPORT, round=p.round, version=1,
            is_current=True, status=ProjectFile.Status.READY)
        self.assertEqual(self.flow(self.auditor, p, 'cost_review_pass').status_code, 200)
        p.refresh_from_db()
        self.assertEqual(p.stage, Project.Stage.FISCAL_REVIEW)
        self.assertFalse(p.drafts.exists())
        self.assertEqual(c.get('/api/projects/inbox/').data['doing']['count'], 0)

    def test_hold_rejects_when_no_action_of_mine(self):
        """申报单位在费用评审环节没有动作，暂存等于给自己摆个假待办。"""
        r = self.client_for(self.applicant).post(
            f'/api/projects/{self.p.id}/draft/',
            {'action_key': 'hold', 'payload': {'note': 'x'}}, format='json')
        self.assertEqual(r.status_code, 400, r.data)


class ProjectArchivePackageTest(FlowTestBase):
    """结项归档：一个项目一份完整资料，结构说清楚，别把材料漏掉。"""

    def setUp(self):
        """归档包会真落盘。测试必须指到临时目录，否则每个用例都在
        data/exports/ 里留一个包，几次跑下来就把真实目录塞满。"""
        super().setUp()
        self._tmp = tempfile.mkdtemp()
        self._ov = override_settings(DATA_DIR=Path(self._tmp))
        self._ov.enable()

    def tearDown(self):
        self._ov.disable()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def test_archive_package_has_readme_and_process(self):
        import io
        import zipfile

        p = self.make_project()
        # 归档包只对已验收项目开放（用户口径：半套材料一律不得导出）
        p.stage = Project.Stage.ACCEPTED
        p.save(update_fields=['stage', 'updated'])
        r = self.client_for(self.applicant).get(f'/api/projects/{p.id}/archive/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('attachment', r['Content-Disposition'])
        blob = b''.join(r.streaming_content) if r.streaming else r.content
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
            self.assertIn('README.txt', names)
            self.assertIn('04_过程/项目信息.json', names)
            self.assertIn('04_过程/流转记录.json', names)
            readme = zf.read('README.txt').decode('utf-8')
            self.assertIn(p.code, readme)
            self.assertIn('01_申报材料/', readme)

    def test_archive_package_refuses_unaccepted_stages(self):
        """验收之前一律不给归档包，管理员也不例外。"""
        p = self.make_project()
        for stage in (Project.Stage.DRAFT, Project.Stage.PRECHECK,
                      Project.Stage.ACCEPTANCE, Project.Stage.RESERVE,
                      Project.Stage.REJECTED):
            p.stage = stage
            p.save(update_fields=['stage', 'updated'])
            for user in (self.applicant, self.reviewer):
                r = self.client_for(user).get(f'/api/projects/{p.id}/archive/')
                self.assertEqual(r.status_code, 403, (stage, user, r.data))
                self.assertIn('半套材料', r.data['detail'])

    def test_archive_package_refuses_other_units_project(self):
        p = self.make_project()
        other = User.objects.create_user(username='other', password='x',
                                         role=User.Role.APPLICANT, unit='教育局')
        r = self.client_for(other).get(f'/api/projects/{p.id}/archive/')
        self.assertEqual(r.status_code, 404)


class ProjectFolderStorageTest(FlowTestBase):
    """文件按项目归到一个文件夹：编号在前、按用途分三块。

    按月分目录时，同一项目三处材料分散在 media/project、generated、review
    三个年月的目录里，要拷给审计得在几千个文件里挑。项目本身就是归档单元，
    文件夹就该以它命名。
    """

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp()
        self._ov = override_settings(MEDIA_ROOT=Path(self._tmp))
        self._ov.enable()

    def tearDown(self):
        self._ov.disable()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def test_material_lands_in_project_folder(self):
        p = self.make_project()
        self.upload(p)
        f = p.files.order_by('id').first()
        name = f.file.name
        self.assertTrue(name.startswith(f'projects/{p.code}_'), name)
        self.assertIn('/01_申报材料/', name)
        self.assertIn('/01_申报中/', name)   # 上传时所处的环节
        self.assertTrue(Path(f.file.path).exists())

    def test_generated_and_review_share_the_folder(self):
        from .models import ProjectDocument

        p = self.make_project()
        doc = ProjectDocument.objects.create(
            project=p, kind='approval', name='立项批复.docx',
            file=SimpleUploadedFile('立项批复.docx', b'doc'))
        rev = ReviewFile.objects.create(
            project=p, name='专家意见表.pdf',
            file=SimpleUploadedFile('专家意见表.pdf', b'rev'))
        prefix = f'projects/{p.code}_'
        self.assertTrue(doc.file.name.startswith(prefix), doc.file.name)
        self.assertTrue(rev.file.name.startswith(prefix), rev.file.name)
        self.assertIn('/02_生成文书/', doc.file.name)
        self.assertIn('/03_评审会材料/', rev.file.name)
        # 三块在同一层：拷走一个文件夹就拿到了这个项目的全部资料
        self.assertEqual(Path(doc.file.path).parent.parent, Path(rev.file.path).parent.parent)

    def test_delete_project_removes_folder(self):
        """删项目时把它的空文件夹一起收掉，别在 media/projects 下留一排空壳。"""
        p = self.make_project()
        self.upload(p)
        folder = (Path(self._tmp) / 'projects'
                  / f'{p.code}_市不动产登记信息平台升级改造项目')
        self.assertTrue(folder.exists())
        p.delete()
        self.assertFalse(folder.exists(), '删项目后文件夹还在')

    def test_stage_folder_follows_flow_order(self):
        from .storage import stage_folder

        self.assertEqual(stage_folder('draft'), '01_申报中')
        self.assertEqual(stage_folder('precheck'), '02_预审中')
        self.assertEqual(stage_folder(''), '00_未标环节')


class ContactPhoneTest(FlowTestBase):
    """联系人这一栏只收手机号：座机收不到短信，填了等于没填。"""

    def test_landline_rejected_on_create(self):
        c = self.client_for(self.applicant)
        r = c.post('/api/projects/', {
            'name': '固话来电测试项目', 'unit': '财政局', 'year': 2026,
            'category': '自然资源', 'contact_name': '王经办',
            'contact_phone': '0990-6220000',
            'investment': 100, 'duration_months': 12,
            'summary': '测试' * 20, 'necessity': '测试', 'basis': '测试',
        }, format='json')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('手机号', str(r.data))

    def test_landline_rejected_on_submit(self):
        p = self.make_project()
        self.upload(p)
        # 绕过接口校验直接改库：模拟「老数据里存的是固话」
        Project.objects.filter(pk=p.pk).update(contact_phone='0990-6220000')
        p.refresh_from_db()
        r = self.flow(self.applicant, p, 'submit', note='提交')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('手机号', str(r.data))


class DraftVisibilityTest(FlowTestBase):
    """#22 草稿隔离：未提交的项目只对申报人和管理员可见。"""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_user(
            username='u_admin_dv', password='Pm@12345678', name='管理员',
            role=User.Role.ADMIN)

    def test_draft_only_in_todo_and_detail(self):
        """申报中的单进不了台账，但申报人能在待办箱与详情页里看到。

        台账（已办项目 / 统计 / 导出）只列已经报出去的件 —— 申报中还没提交的
        申报单躺在申报人手里。草稿本身仍只对申报人与管理员开放：初审、审核岗
        连详情都是 404；管理员要排查数据得显式带 include_draft=1。
        """
        p = self.make_project()
        self.assertEqual(p.stage, Project.Stage.DRAFT)
        # 台账不列草稿：申报人与管理员一样
        for u in (self.applicant, self.admin):
            r = self.client_for(u).get('/api/projects/')
            self.assertNotIn(p.id, [x['id'] for x in r.data['results']])
        # 申报人：待办箱里有它，详情打得开
        r = self.client_for(self.applicant).get('/api/projects/inbox/')
        self.assertIn(p.id, [x['id'] for x in r.data['todos']['items']])
        self.assertEqual(
            self.client_for(self.applicant).get(f'/api/projects/{p.id}/').status_code, 200)
        # 初审/审核岗不可见（列表与详情都收窄）
        for u in (self.reviewer, self.auditor):
            r = self.client_for(u).get('/api/projects/')
            self.assertNotIn(p.id, [x['id'] for x in r.data['results']])
            self.assertEqual(self.client_for(u).get(f'/api/projects/{p.id}/').status_code, 404)
        # 管理员排查数据：显式带 include_draft=1 才看得到
        r = self.client_for(self.admin).get('/api/projects/?include_draft=1')
        self.assertIn(p.id, [x['id'] for x in r.data['results']])


class UploadQuotaTest(FlowTestBase):
    """#23 上传配额：单次 200MB / 10 份，累计 5GB。"""

    def test_helper_rejects_too_many_files(self):
        from .upload_quota import check_upload_quota
        files = [SimpleUploadedFile(f'f{i}.txt', b'x') for i in range(11)]
        msg = check_upload_quota(self.applicant, files, used=0)
        self.assertIsNotNone(msg)
        self.assertIn('10 份', msg)

    def test_helper_rejects_batch_over_200mb(self):
        from .upload_quota import check_upload_quota

        class Fake:
            def __init__(self, size):
                self.size = size

        msg = check_upload_quota(self.applicant,
                                 [Fake(150 * 1024 * 1024), Fake(60 * 1024 * 1024)],
                                 used=0)
        self.assertIsNotNone(msg)
        self.assertIn('200MB', msg)

    def test_helper_rejects_cumulative_over_5gb(self):
        from .upload_quota import check_upload_quota

        class Fake:
            size = 1024 * 1024

        msg = check_upload_quota(self.applicant, [Fake()],
                                 used=5 * 1024 ** 3)
        self.assertIsNotNone(msg)
        self.assertIn('累计上传额度', msg)

    def test_helper_allows_normal_batch(self):
        from .upload_quota import check_upload_quota

        class Fake:
            size = 30 * 1024 * 1024

        self.assertIsNone(check_upload_quota(self.applicant, [Fake(), Fake()], used=0))

    def test_upload_endpoint_enforces_file_count(self):
        p = self.make_project()
        files = [SimpleUploadedFile(f'f{i}.txt', b'tiny') for i in range(11)]
        r = self.client_for(self.applicant).post(
            f'/api/projects/{p.id}/files/', {'files': files}, format='multipart')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('10 份', r.data['detail'])


class BudgetAmountTest(FlowTestBase):
    """核定预算：财评结论写入，财政可改，批复取这个数（2026-11-24 口径）。

    原来是直接拿可研投资写批复，财评核减的钱根本没地方落 —— 批下来的
    投资和评估报告上的数对不上，档案里两处数字打架。
    """

    def _ready(self):
        p = self.make_project()
        p.stage = Project.Stage.COST_REVIEW
        p.investment = Decimal('120')
        p.save(update_fields=['stage', 'investment'])
        ProjectDocument.objects.create(project=p, kind='cost_review_task',
                                       name='费用评审任务书.pdf', stage=p.stage)
        ProjectFile.objects.create(project=p, filename='费用评估报告.pdf',
                                   doc_type='cost_report', round=0, stage=p.stage)
        return p

    def test_cost_pass_records_audit_amount(self):
        p = self._ready()
        r = self.flow(self.auditor, p, 'cost_review_pass', audit_amount='108.6')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(float(p.budget_amount), 108.6)
        self.assertEqual(p.budget_wan, 108.6)

    def test_fiscal_can_override_budget(self):
        p = self._ready()
        self.flow(self.auditor, p, 'cost_review_pass', audit_amount='108.6')
        p.refresh_from_db()
        p.stage = Project.Stage.FISCAL_REVIEW
        p.save(update_fields=['stage'])
        r = self.flow(self.auditor, p, 'fiscal_pass',
                      note='已列入 2026 年市本级财政预算，安排资金 100 万元',
                      budget_amount='100')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(float(p.budget_amount), 100)

    def test_amount_must_be_positive(self):
        p = self._ready()
        r = self.flow(self.auditor, p, 'cost_review_pass', audit_amount='0')
        self.assertEqual(r.status_code, 400, r.data)


class StageRoleBindingTest(FlowTestBase):
    """环节可指定具体岗位：立项指给「立项审批岗」后，初审岗就不该看到这个按钮。"""

    def setUp(self):
        super().setUp()
        self.p = self.make_project()
        self.p.stage = Project.Stage.APPROVAL
        self.p.handler = None
        self.p.save(update_fields=['stage', 'handler'])
        self.approver = Role.objects.create(code='approver', name='立项审批岗',
                                            scope=User.Role.REVIEWER)
        StageSla.objects.update_or_create(
            stage=Project.Stage.APPROVAL, defaults={'days': 5, 'role': 'approver'})

    def _keys(self, user):
        d = self.client_for(user).get(f'/api/projects/{self.p.id}/').data
        return [a['key'] for a in d['actions']]

    def test_other_role_in_same_scope_cannot_act(self):
        self.assertNotIn('approval_issue', self._keys(self.reviewer))

    def test_bound_role_can_act(self):
        u = self.reviewer
        u.role_ref = self.approver
        u.save(update_fields=['role_ref'])
        self.assertIn('approval_issue', self._keys(u))


class WithdrawGateTest(FlowTestBase):
    """初审一动，撤回就没有了：预审报告已出，或初审已存过稿。"""

    def test_withdraw_available_before_precheck(self):
        p = self.make_project()
        self.upload(p)
        self.flow(self.applicant, p, 'submit')
        p.refresh_from_db()
        d = self.client_for(self.applicant).get(f'/api/projects/{p.id}/').data
        self.assertIn('withdraw', [a['key'] for a in d['actions']])

    def test_withdraw_gone_after_report(self):
        p = self.make_project()
        self.upload(p)
        self.flow(self.applicant, p, 'submit')
        p.refresh_from_db()
        PrecheckReport.objects.create(project=p, round=0, level='rule',
                                      verdict='pass', counts={}, items=[])
        d = self.client_for(self.applicant).get(f'/api/projects/{p.id}/').data
        self.assertNotIn('withdraw', [a['key'] for a in d['actions']])
