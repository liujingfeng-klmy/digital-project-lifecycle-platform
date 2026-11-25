"""第二批修复的回归测试：多轮隔离（报告 / 证明 / 材料 / 统计口径）。"""
import hashlib

from django.test import TestCase

from apps.core.models import Role, User
from . import flow
from .docgen import generate_data_collect
from .models import (PrecheckReport, Project, ProjectDocument, ProjectFile,
                     ProjectEvent)
from .report import build as build_report


class FixB2Base(TestCase):
    def setUp(self):
        for code, label in (('applicant', '申报单位'), ('reviewer', '初审岗'),
                            ('auditor', '审核岗'), ('admin', '管理员')):
            Role.objects.get_or_create(
                code=code, defaults={'name': label, 'scope': code, 'builtin': True})
        self.applicant = User.objects.create_user(username='b2a', role='applicant', unit='教育局')
        self.reviewer = User.objects.create_user(username='b2r', role='reviewer')
        self.auditor = User.objects.create_user(username='b2u', role='auditor')

    def make_project(self, stage=Project.Stage.DRAFT, round_=0):
        return Project.objects.create(
            name='测试项目', unit='教育局', applicant=self.applicant,
            stage=stage, round=round_, handler_role=flow.handler_role_of(stage))

    def add_cost_task(self, p):
        ProjectDocument.objects.create(
            project=p, kind='cost_review_task', name='任务书.docx',
            file='generated/task.docx', stage=Project.Stage.COST_REVIEW)

    def add_report(self, p, round_, name='评估报告.pdf'):
        return ProjectFile.objects.create(
            project=p, file=f'projects/x/{round_}_{name}', filename=name,
            doc_type=ProjectFile.DocType.COST_REPORT, uploaded_by=self.auditor,
            round=round_, status=ProjectFile.Status.READY)


class CostReviewRoundTest(FixB2Base):
    """#5：费用评审只认本轮报告。"""

    def test_old_round_report_cannot_pass_current_round(self):
        p = self.make_project(Project.Stage.COST_REVIEW, round_=1)
        self.add_cost_task(p)
        self.add_report(p, round_=0, name='上一轮报告.pdf')
        with self.assertRaisesMessage(flow.FlowError, '本轮'):
            flow._h_cost_review_pass(p, self.auditor, {})

    def test_current_round_report_can_pass(self):
        p = self.make_project(Project.Stage.COST_REVIEW, round_=1)
        self.add_cost_task(p)
        self.add_report(p, round_=1, name='本轮报告.pdf')
        payload, extra = flow._h_cost_review_pass(p, self.auditor, {})
        self.assertEqual(payload['audit'], 'pass')


class DataCollectScopeTest(FixB2Base):
    """#6：证明必须对应当前归集口径。"""

    def test_proof_matches_current_scope(self):
        p = self.make_project(Project.Stage.ACCEPTANCE)
        p.data_collect_scope = '已归集业务数据 12 万条'
        p.save(update_fields=['data_collect_scope'])
        generate_data_collect(p, {}, self.auditor)
        flow._h_accept(p, self.auditor, {})   # 不抛异常即通过

    def test_stale_proof_is_rejected(self):
        p = self.make_project(Project.Stage.ACCEPTANCE)
        p.data_collect_scope = '原始归集说明'
        p.save(update_fields=['data_collect_scope'])
        generate_data_collect(p, {}, self.auditor)
        # 退回补充后单位重填了归集说明
        p.data_collect_scope = '重填后的归集说明，与旧证明完全不同'
        p.save(update_fields=['data_collect_scope'])
        with self.assertRaisesMessage(flow.FlowError, '不一致'):
            flow._h_accept(p, self.auditor, {})

    def test_no_proof_is_rejected(self):
        p = self.make_project(Project.Stage.ACCEPTANCE)
        p.data_collect_scope = '有归集说明'
        p.save(update_fields=['data_collect_scope'])
        with self.assertRaisesMessage(flow.FlowError, '还没有出具'):
            flow._h_accept(p, self.auditor, {})


class CollectFilterTest(FixB2Base):
    """#10：查重与 AI 通读只看当前版本正文，不混入旧版与费用报告。"""

    def test_collect_excludes_cost_report_and_stale_version(self):
        from . import precheck
        p = self.make_project(Project.Stage.PRECHECK)
        ProjectFile.objects.create(
            project=p, file='projects/x/a.txt', filename='旧版可研.txt',
            doc_type=ProjectFile.DocType.FEASIBILITY,
            status=ProjectFile.Status.READY, is_current=False,
            text='旧版正文' * 100, sections=[{'title': '旧', 'text': '旧版'}])
        ProjectFile.objects.create(
            project=p, file='projects/x/b.txt', filename='新版可研.txt',
            doc_type=ProjectFile.DocType.FEASIBILITY,
            status=ProjectFile.Status.READY, is_current=True,
            text='新版正文' * 100, sections=[{'title': '新', 'text': '新版'}])
        ProjectFile.objects.create(
            project=p, file='projects/x/c.txt', filename='评估报告.txt',
            doc_type=ProjectFile.DocType.COST_REPORT,
            status=ProjectFile.Status.READY, is_current=True,
            text='评估报告正文' * 100, sections=[{'title': '评', 'text': '报告'}])
        files, text, sections = precheck.collect(p, corpus_only=True)
        self.assertEqual(len(files), 1)
        self.assertIn('新版正文', text)
        self.assertNotIn('旧版正文', text)
        self.assertNotIn('评估报告正文', text)

    def test_rule_check_keeps_budget_for_investment_check(self):
        from . import precheck
        p = self.make_project(Project.Stage.PRECHECK)
        ProjectFile.objects.create(
            project=p, file='projects/x/b.txt', filename='概算.txt',
            doc_type=ProjectFile.DocType.BUDGET,
            status=ProjectFile.Status.READY, is_current=True, text='合计 100 万元')
        files, _text, _sections = precheck.collect(p)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].doc_type, ProjectFile.DocType.BUDGET)


class ReportMirrorTest(FixB2Base):
    """#11/#12/#13：统计口径。"""

    def test_reserve_reactivate_clears_fiscal_note(self):
        p = self.make_project(Project.Stage.RESERVE)
        p.fiscal_result = 'reject'
        p.fiscal_note = '无资金安排的理由'
        p.save(update_fields=['fiscal_result', 'fiscal_note'])
        flow._h_reserve_reactivate(p, self.reviewer, {'note': '今年有资金了，重新纳入'})
        p.refresh_from_db()
        self.assertEqual(p.fiscal_result, '')
        self.assertEqual(p.fiscal_note, '')

    def test_reserve_is_terminal_in_stats(self):
        p = self.make_project(Project.Stage.RESERVE)
        p.submitted_at = p.created
        p.save(update_fields=['submitted_at'])
        data = build_report(Project.objects.filter(pk=p.pk))
        self.assertNotIn(p.pk, [x.get('id') for x in data.get('stuck', [])]
                         if isinstance(data.get('stuck'), list) else [])

    def test_feedback_not_counted_as_return(self):
        p = self.make_project(Project.Stage.PRECHECK)
        p.submitted_at = p.created
        p.save(update_fields=['submitted_at'])
        ProjectEvent.objects.create(project=p, action='submit', actor=self.applicant)
        ProjectEvent.objects.create(project=p, action='cost_review_return',
                                    actor=self.auditor)
        data = build_report(Project.objects.filter(pk=p.pk))
        rr = data['return_rate']
        self.assertEqual(rr['returned'], 0)
        self.assertEqual(rr['feedback_projects'], 1)
