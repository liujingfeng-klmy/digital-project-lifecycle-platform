"""第三批修复的回归测试：前后端一致性中的后端约束部分。"""
from django.test import TestCase

from apps.core.models import Role, User
from . import flow
from .models import SupplementNotice, Project


class FixB3Base(TestCase):
    def setUp(self):
        for code, label in (('applicant', '申报单位'), ('reviewer', '初审岗'),
                            ('auditor', '审核岗')):
            Role.objects.get_or_create(
                code=code, defaults={'name': label, 'scope': code, 'builtin': True})
        self.applicant = User.objects.create_user(username='b3a', role='applicant', unit='教育局')
        self.auditor = User.objects.create_user(username='b3u', role='auditor')

    def make_project(self, stage, round_=0):
        return Project.objects.create(
            name='测试项目', unit='教育局', applicant=self.applicant,
            stage=stage, round=round_, handler_role=flow.handler_role_of(stage))


class FormMetaTest(FixB3Base):
    def test_withdraw_form_has_min(self):
        spec = next(a for a in flow.ACTIONS if a['key'] == 'withdraw')
        note = next(f for f in spec['form'] if f['key'] == 'note')
        self.assertEqual(note.get('min'), 5)

    def test_withdraw_rejects_short_note(self):
        p = self.make_project(Project.Stage.PRECHECK)
        try:
            flow._h_withdraw(p, self.applicant, {'note': '太短'})
            self.fail('应拒绝少于 5 字的撤回原因')
        except flow.FlowError:
            pass


class ResubmitRequiresMaterialTest(FixB3Base):
    def test_empty_resubmit_rejected(self):
        p = self.make_project(Project.Stage.SUPPLEMENT, round_=1)
        SupplementNotice.objects.create(project=p, round=1, status=SupplementNotice.Status.OPEN)
        try:
            flow._h_resubmit(p, self.applicant, {'note': '已修改'})
            self.fail('空手补正应被拒绝')
        except flow.FlowError as exc:
            self.assertIn('本轮还没有上传', str(exc))
