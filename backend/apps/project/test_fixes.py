"""本轮修复的回归测试。

覆盖点：涉敏材料拦截 AI、评审会票数口径、编号不复用、
删除权限、补正条数、日期校验、统计口径。
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.aicore import llm as llm_mod
from apps.core.models import User
from apps.project import flow, precheck, report
from apps.project.models import (Project, ProjectCodeSequence, ProjectEvent,
                                 ProjectFile, ReviewFile, SupplementNotice)
from apps.project.views import next_code

CFG = {
    'llm': {'enabled': False, 'base_url': '', 'model': '', 'api_key': ''},
    'review': {'pass_score': 85, 'revise_score': 70},
    'flow': {'sign_mode': 'all'},
    'sensitive': {'skip_ai': True},
}


def cfg(mode='all', skip_ai=True):
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in CFG.items()}
    out['flow'] = {'sign_mode': mode}
    out['sensitive'] = {'skip_ai': skip_ai}
    return out


class Base(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('admin_t', password='Pw@12345678',
                                              role='admin', name='管理员')
        self.applicant = User.objects.create_user('app_t', password='Pw@12345678',
                                                  role='applicant', name='申报人',
                                                  unit='财政局')
        self.reviewer = User.objects.create_user('rev_t', password='Pw@12345678',
                                                 role='reviewer', name='初审')
        self.auditor = User.objects.create_user('aud_t', password='Pw@12345678',
                                                role='auditor', name='审核')

    def make_project(self, stage='draft', applicant=None, name='测试申报项目', **kw):
        p = Project.objects.create(
            name=name, unit='财政局', applicant=applicant or self.applicant,
            investment=100, summary='建设内容' * 10, stage=stage,
            handler_role='', code=kw.pop('code', f'XM2026-{Project.objects.count() + 1:03d}'),
            **kw)
        return p


class SensitiveMaterialTest(Base):
    """问题 2：涉敏标记必须真的拦住材料出网。"""

    def setUp(self):
        super().setUp()
        self.p = self.make_project(stage=Project.Stage.PRECHECK)

    @patch.object(llm_mod, 'load_config')
    def test_sensitive_project_blocks_ai(self, mock_cfg):
        mock_cfg.return_value = cfg(skip_ai=True)
        self.p.sensitive = True
        self.p.save(update_fields=['sensitive'])
        with self.assertRaisesMessage(RuntimeError, '涉敏'):
            precheck.run_ai_review(self.p, self.reviewer)

    @patch.object(llm_mod, 'load_config')
    def test_normal_project_still_checks_llm_enabled(self, mock_cfg):
        mock_cfg.return_value = cfg(skip_ai=True)
        with self.assertRaisesMessage(RuntimeError, '未启用'):
            precheck.run_ai_review(self.p, self.reviewer)

    @patch.object(llm_mod, 'load_config')
    def test_sensitive_can_run_when_switch_off(self, mock_cfg):
        """管理员显式关掉 skip_ai 后，涉敏项目按普通项目处理（走到「未启用」）。"""
        mock_cfg.return_value = cfg(skip_ai=False)
        self.p.sensitive = True
        self.p.save(update_fields=['sensitive'])
        with self.assertRaisesMessage(RuntimeError, '未启用'):
            precheck.run_ai_review(self.p, self.reviewer)


class ReviewVoteTest(Base):
    """评审结论口径（2026-11-19 定）：

    评审会开完收表，初审岗一人录入三档票数（通过/修改后通过/不通过），
    系统按「通过（含修改后通过）过半」自动出结论。没有专家账号、没有逐人
    签署、没有评分，结论也不由录入人手选。
    """

    def setUp(self):
        super().setUp()
        self.p = self.make_project(stage=Project.Stage.EXPERT_REVIEW)
        ReviewFile.objects.create(project=self.p, file='review/202611/a.pdf',
                                  name='专家甲意见表.pdf', kind='expert')
        ReviewFile.objects.create(project=self.p, file='review/202611/b.pdf',
                                  name='汇总意见表.pdf', kind='summary')

    def conclude(self, agree, modified, reject, note='评审会意见，内容足够十个字以上。'):
        return flow.transition(self.p, self.reviewer, 'conclude', {
            'votes': {'agree': agree, 'modified': modified, 'reject': reject},
            'note': note})

    def test_two_of_three_pass(self):
        self.conclude(2, 0, 1)
        self.p.refresh_from_db()
        self.assertEqual(self.p.review_result, Project.ReviewResult.PASS)
        self.assertEqual(self.p.stage, Project.Stage.COST_REVIEW)
        self.assertEqual((self.p.review_agree, self.p.review_modified,
                          self.p.review_reject), (2, 0, 1))

    def test_modified_counts_as_agree(self):
        self.conclude(1, 1, 1)
        self.p.refresh_from_db()
        self.assertEqual(self.p.review_result, Project.ReviewResult.MODIFIED)
        # 修改后通过要先改材料，不能直接进审核
        self.assertEqual(self.p.stage, Project.Stage.REVIEW_SUPPLEMENT)
        self.assertTrue(self.p.notices.filter(
            kind='review', status='open').exists())

    def test_majority_reject(self):
        self.conclude(1, 0, 2)
        self.p.refresh_from_db()
        self.assertEqual(self.p.review_result, Project.ReviewResult.REJECT)
        self.assertEqual(self.p.stage, Project.Stage.REJECTED)

    def test_unanimous_is_plain_pass(self):
        """全票同意且无人要求修改，结论就是「通过」，不能记成修改后通过。"""
        self.conclude(5, 0, 0)
        self.p.refresh_from_db()
        self.assertEqual(self.p.review_result, Project.ReviewResult.PASS)

    def test_review_files_required(self):
        """一张签字表都没有，不许出结论 —— 只剩几个数字，事后无法追溯。"""
        self.p.review_files.all().delete()
        with self.assertRaisesMessage(flow.FlowError, '评审会材料'):
            self.conclude(3, 0, 0)

    def test_even_total_blocked(self):
        with self.assertRaisesMessage(flow.FlowError, '偶数'):
            self.conclude(2, 0, 2)

    def test_zero_total_blocked(self):
        with self.assertRaisesMessage(flow.FlowError, '合计为 0'):
            self.conclude(0, 0, 0)

    def test_negative_blocked(self):
        with self.assertRaisesMessage(flow.FlowError, '负数'):
            self.conclude(-1, 2, 2)

    def test_missing_votes_blocked(self):
        with self.assertRaisesMessage(flow.FlowError, '通过'):
            flow.transition(self.p, self.reviewer, 'conclude',
                            {'note': '评审会意见，内容足够十个字以上。'})

    def test_cannot_hand_pick_conclusion(self):
        """结论由票数算；就算前端多塞一个结论字段也改不了结果。"""
        flow.transition(self.p, self.reviewer, 'conclude', {
            'votes': {'agree': 0, 'modified': 0, 'reject': 3},
            'review_result': Project.ReviewResult.PASS,
            'note': '想直接选通过，但票数是不通过'})
        self.p.refresh_from_db()
        self.assertEqual(self.p.review_result, Project.ReviewResult.REJECT)

    def test_event_records_vote_math(self):
        self.conclude(2, 1, 2)
        ev = self.p.events.filter(action='conclude').latest('created')
        self.assertIn('通过 3/5', ev.comment)
        self.assertIn('修改后通过 1', ev.comment)

    def test_revote_replaces_previous_round(self):
        self.conclude(2, 0, 1)
        self.p.refresh_from_db()
        self.p.stage = Project.Stage.EXPERT_REVIEW
        self.p.save(update_fields=['stage'])
        self.conclude(0, 0, 3)
        self.p.refresh_from_db()
        self.assertEqual(self.p.review_result, Project.ReviewResult.REJECT)
        self.assertEqual((self.p.review_agree, self.p.review_modified,
                          self.p.review_reject), (0, 0, 3))

    def test_precheck_pass_needs_no_experts(self):
        """初审通过只要初审意见，不再要求先指定专家。"""
        p = self.make_project(stage=Project.Stage.PRECHECK)
        with self.assertRaisesMessage(flow.FlowError, '规则预审'):
            flow.transition(p, self.reviewer, 'precheck_pass',
                            {'note': '材料齐全，同意提交专家评审'})

    def test_expert_role_removed(self):
        self.assertFalse(hasattr(User.Role, 'EXPERT'))
        self.assertNotIn('sign', flow.ACTION_MAP)
        self.assertNotIn('assign_experts', flow.ACTION_MAP)


class CodeSequenceTest(Base):
    """问题 5：编号不复用。"""

    def test_code_keeps_increasing_after_delete(self):
        first = next_code()
        p = Project.objects.create(name='编号测试项目一', unit='单位',
                                   applicant=self.applicant, code=first)
        second = next_code()
        self.assertNotEqual(first, second)
        p.delete()
        third = next_code()
        self.assertNotEqual(third, first)   # 删掉的号不再回收
        self.assertGreater(third, second)

    def test_sequence_row_created(self):
        next_code()
        year = timezone.now().year
        self.assertTrue(ProjectCodeSequence.objects.filter(year=year).exists())


class DeletePermissionTest(Base):
    """问题 3：非管理员不能删别人单位的草稿。"""

    def test_reviewer_cannot_delete_other_draft(self):
        p = self.make_project(stage=Project.Stage.DRAFT)
        c = APIClient()
        c.force_authenticate(self.reviewer)
        resp = c.delete(f'/api/projects/{p.id}/')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Project.objects.filter(pk=p.pk).exists())

    def test_owner_can_delete_own_draft(self):
        p = self.make_project(stage=Project.Stage.DRAFT)
        c = APIClient()
        c.force_authenticate(self.applicant)
        resp = c.delete(f'/api/projects/{p.id}/')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Project.objects.filter(pk=p.pk).exists())

    def test_admin_can_delete_draft(self):
        p = self.make_project(stage=Project.Stage.DRAFT)
        c = APIClient()
        c.force_authenticate(self.admin)
        resp = c.delete(f'/api/projects/{p.id}/')
        self.assertEqual(resp.status_code, 204)


class NoticeCountTest(Base):
    """问题 9：补正已解决条数不能恒为 0。"""

    def test_resolved_notices_counted(self):
        p = self.make_project(stage=Project.Stage.SUPPLEMENT, round=1)
        SupplementNotice.objects.create(project=p, round=1, items=[{'title': '补事项'}],
                                        status=SupplementNotice.Status.OPEN)
        # 补正必须带本轮材料（审查项 #15）：空手提交会被拦
        ProjectFile.objects.create(
            project=p, file='projects/x/budget.txt', filename='概算明细.txt',
            doc_type=ProjectFile.DocType.BUDGET, uploaded_by=self.applicant,
            round=1, status=ProjectFile.Status.READY)
        flow.transition(p, self.applicant, 'resubmit',
                        {'note': '已按补正单逐条修改，详见附件说明。'})
        event = ProjectEvent.objects.filter(project=p, action='resubmit').first()
        self.assertEqual(event.payload.get('resolved_notices'), 1)


class DateValidationTest(Base):
    """问题 17：非法日期返回业务错误，不是 500。"""

    def test_bad_deadline_rejected(self):
        p = self.make_project(stage=Project.Stage.PRECHECK)
        with self.assertRaisesMessage(flow.FlowError, '日期'):
            flow.transition(p, self.reviewer, 'precheck_return', {
                'items': [{'title': '缺少概算明细'}],
                'deadline': '2026/13/45',
            })

    def test_empty_deadline_allowed(self):
        p = self.make_project(stage=Project.Stage.PRECHECK)
        flow.transition(p, self.reviewer, 'precheck_return', {
            'items': [{'title': '缺少概算明细'}], 'deadline': '',
        })
        self.assertEqual(p.notices.count(), 1)


class ReportMetricTest(Base):
    """问题 10 / 11：退回率分母与阶段耗时口径。"""

    def test_return_rate_uses_submitted_only(self):
        submitted = self.make_project(name='已提交项目', code='XM2026-901')
        submitted.submitted_at = timezone.now()
        submitted.save(update_fields=['submitted_at'])
        self.make_project(name='草稿项目一', code='XM2026-902')
        self.make_project(name='草稿项目二', code='XM2026-903')

        now = timezone.now()
        ProjectEvent.objects.create(project=submitted, action='submit',
                                    action_label='提交申报', from_stage='draft',
                                    to_stage='precheck', created=now - timedelta(days=9))
        ProjectEvent.objects.create(project=submitted, action='precheck_return',
                                    action_label='退回补正', from_stage='precheck',
                                    to_stage='supplement', created=now - timedelta(days=8))
        data = report.build(Project.objects.all())
        self.assertEqual(data['return_rate']['submitted'], 1)
        self.assertEqual(data['return_rate']['returned'], 1)
        self.assertEqual(data['return_rate']['rate'], 1.0)

    def test_stage_finished_counts_projects_not_visits(self):
        p = self.make_project(name='多轮补正项目', code='XM2026-911')
        now = timezone.now()
        seq = [('submit', 'draft', 'precheck', 10),
               ('precheck_return', 'precheck', 'supplement', 8),
               ('resubmit', 'supplement', 'precheck', 6),
               ('precheck_pass', 'precheck', 'expert_review', 4)]
        for action, f, t, days in seq:
            ProjectEvent.objects.create(project=p, action=action, action_label=action,
                                        from_stage=f, to_stage=t,
                                        created=now - timedelta(days=days))
        data = report.build(Project.objects.all())
        row = next(r for r in data['stage_days'] if r['key'] == 'precheck')
        self.assertEqual(row['finished'], 1)   # 走过该阶段的项目数
        self.assertEqual(row['visits'], 2)     # 进入次数（两轮）
