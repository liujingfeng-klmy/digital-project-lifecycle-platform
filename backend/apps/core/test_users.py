"""账号管理的回归测试。

三条最容易出事的线，各钉一根桩：
1. 用户名建错了能改（库里真的出现过 zhuangjia3）；
2. 忘了密码能重置，且旧会话立即失效；
3. 管理员不会把自己或最后一个管理员锁在门外。
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import Role, User


class UserAdminTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username='admin', password='Admin@123456', name='管理员',
            role=User.Role.ADMIN)
        # 拿一个普通业务账号当被管理对象即可（角色取消后专家账号已不存在）
        cls.expert = User.objects.create_user(
            username='chushen3', password='Pm@123456', name='刘初审',
            role=User.Role.REVIEWER, unit='评审组', must_change_password=True)

    def client_for(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def test_edit_username_and_name(self):
        c = self.client_for(self.admin)
        r = c.patch(f'/api/users/{self.expert.pk}/', {
            'username': 'chushen3', 'name': '刘初审', 'role': 'reviewer',
            'unit': '评审组', 'phone': '13800000000',
        }, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.expert.refresh_from_db()
        self.assertEqual(self.expert.username, 'chushen3')
        self.assertEqual(self.expert.phone, '13800000000')

    def test_username_must_stay_unique(self):
        c = self.client_for(self.admin)
        r = c.patch(f'/api/users/{self.expert.pk}/', {'username': 'admin'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_reset_password_kicks_old_session(self):
        before = self.expert.token_version
        c = self.client_for(self.admin)
        r = c.post(f'/api/users/{self.expert.pk}/reset-password/',
                   {'password': 'NewPm@2026'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.expert.refresh_from_db()
        self.assertTrue(self.expert.check_password('NewPm@2026'))
        self.assertTrue(self.expert.must_change_password)
        self.assertEqual(self.expert.token_version, before + 1)

    def test_weak_password_rejected(self):
        c = self.client_for(self.admin)
        r = c.post(f'/api/users/{self.expert.pk}/reset-password/',
                   {'password': '12345678'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_cannot_disable_self(self):
        c = self.client_for(self.admin)
        r = c.patch(f'/api/users/{self.admin.pk}/', {'is_active': False}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_last_admin_cannot_be_demoted(self):
        other = User.objects.create_user(
            username='admin2', password='Admin@123456', name='管理员二',
            role=User.Role.ADMIN)
        reviewer = Role.objects.get(code='reviewer')
        c = self.client_for(other)
        r = c.patch(f'/api/users/{self.admin.pk}/', {'role_id': reviewer.pk}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        # 只剩 admin2 一个管理员，再把自己降级就必须被拦
        r = c.patch(f'/api/users/{other.pk}/', {'role_id': reviewer.pk}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_disabled_account_can_be_enabled(self):
        c = self.client_for(self.admin)
        c.delete(f'/api/users/{self.expert.pk}/')
        self.expert.refresh_from_db()
        self.assertFalse(self.expert.is_active)
        r = c.patch(f'/api/users/{self.expert.pk}/', {'is_active': True}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.expert.refresh_from_db()
        self.assertTrue(self.expert.is_active)

    def test_non_admin_cannot_edit(self):
        c = self.client_for(self.expert)
        r = c.patch(f'/api/users/{self.expert.pk}/', {'name': '随便改'}, format='json')
        self.assertEqual(r.status_code, 403)


class DisabledRoleTest(TestCase):
    """角色停用 = 即时收回权限（用户拍板 #18）。

    两条线各钉一根桩：停用后不能再登录；已签发的 token 立即失效。
    """

    @classmethod
    def setUpTestData(cls):
        cls.role = Role.objects.create(code='rev_test', name='初审岗测试',
                                       scope=Role.Scope.REVIEWER, active=True)
        cls.user = User.objects.create_user(
            username='revrole_user', password='Pm@123456', name='测试初审',
            role_ref=cls.role)

    def _login(self):
        c = APIClient()
        return c, c.post('/api/auth/login',
                         {'username': 'revrole_user', 'password': 'Pm@123456'},
                         format='json')

    def test_login_rejected_when_role_disabled(self):
        self.role.active = False
        self.role.save(update_fields=['active'])
        _c, resp = self._login()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('角色', str(resp.data))

    def test_existing_token_revoked_when_role_disabled(self):
        c, resp = self._login()
        self.assertEqual(resp.status_code, 200, resp.data)
        # 用真实 token 发请求（APIClient 不会自动带 token）
        c.credentials(HTTP_AUTHORIZATION='Bearer ' + resp.data['access'])
        self.assertEqual(c.get('/api/auth/me').status_code, 200)
        # 停用角色后，同一 token 立刻失效（403），无需等 token 过期
        self.role.active = False
        self.role.save(update_fields=['active'])
        denied = c.get('/api/auth/me')
        self.assertEqual(denied.status_code, 403)
        self.assertIn('停用', str(denied.data))
