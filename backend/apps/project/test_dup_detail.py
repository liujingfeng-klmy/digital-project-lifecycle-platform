"""重复建设「逐项对照」的回归测试。

起因：初审只看到一句「部分业务功能重合」，不知道重合的是哪几条功能、
哪几段原文，只能把两份可研从头翻一遍。这里守住三件事：
  1. 提交申报后 dup_check 里必须带功能对照、章节对照、原文片段；
  2. 对照里的功能项要带两侧章节，否则看不出这一项落在哪一章；
  3. 重跑接口只对预审类角色开放，申报单位不能给自己免检。

跑法：.venv/bin/python manage.py test apps.project.test_dup_detail -v 2
"""
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.archive.models import ArchiveFile
from apps.archive.services import ingest_upload
from apps.core.models import User

from .models import Project

SAMPLES = Path(settings.PROJECT_ROOT) / '样例数据'
CUR = SAMPLES / '01_市不动产登记信息平台_可行性研究报告.docx'
PREV = SAMPLES / '02_不动产登记一网通办服务平台_可行性研究报告.docx'


class DupDetailTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.applicant = User.objects.create_user(
            username='d_app', password='Pm@12345678', name='申报员',
            role=User.Role.APPLICANT, unit='财政局')
        cls.reviewer = User.objects.create_user(
            username='d_rev', password='Pm@12345678', name='初审员',
            role=User.Role.REVIEWER, unit='数据管理局')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        self.tmp.cleanup()

    def client_for(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _archive_prev(self):
        if not PREV.exists():
            self.skipTest('样例材料不存在')
        res = ingest_upload(
            SimpleUploadedFile(PREV.name, PREV.read_bytes()), self.reviewer)
        self.assertTrue(res['ok'], res.get('message'))
        self.assertTrue(ArchiveFile.objects.filter(status='ready').exists())

    def _submitted_project(self):
        if not CUR.exists():
            self.skipTest('样例材料不存在')
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
        pid = r.data['id']
        up = c.post(f'/api/projects/{pid}/files/',
                    {'files': [SimpleUploadedFile(CUR.name, CUR.read_bytes())]},
                    format='multipart')
        self.assertEqual(up.status_code, 200, up.data)
        sub = c.post(f'/api/projects/{pid}/flow/',
                     {'action': 'submit', 'note': '首次申报'}, format='json')
        self.assertEqual(sub.status_code, 200, sub.data)
        return Project.objects.get(pk=pid)

    def test_dup_check_carries_item_level_comparison(self):
        self._archive_prev()
        p = self._submitted_project()

        dup = p.dup_check or {}
        self.assertEqual(dup.get('version'), 3, '结构版本 3 起只比建设内容章')
        self.assertTrue(dup.get('ranking'), '同类项目应被列为候选')

        top = dup['ranking'][0]
        for key in ('functions', 'sections', 'snippets'):
            self.assertIn(key, top, f'缺少 {key}，初审仍然只能翻原文')

        # 章节对照：有分数、有两侧字数，才有可比性
        self.assertTrue(top['sections'])
        sec = top['sections'][0]
        self.assertIn('label', sec)
        self.assertGreater(sec['score'], 0)

        # 功能对照：每一项都要带两侧章节，否则看不出重复落在哪一章
        self.assertTrue(top['functions']['pairs'], '同类可研应能对上功能')
        pair = top['functions']['pairs'][0]
        self.assertIn('query_section', pair)
        self.assertIn('hit_section', pair)

        # 原文片段：两侧都要有正文，才能左右对照
        self.assertTrue(top['snippets'], '同类可研应有相似段落')
        snip = top['snippets'][0]
        self.assertGreater(len(snip['query_text']), 20)
        self.assertGreater(len(snip['hit_text']), 20)

        # 配对数量与截断后的明细要分开记，界面才说得清「共几项、列出几项」
        self.assertGreaterEqual(top['functions']['pair_count'],
                                len(top['functions']['pairs']))

    def test_refresh_api_role_gate(self):
        self._archive_prev()
        p = self._submitted_project()

        r = self.client_for(self.applicant).post(f'/api/projects/{p.id}/dup-refresh/')
        self.assertEqual(r.status_code, 403)

        r = self.client_for(self.reviewer).post(f'/api/projects/{p.id}/dup-refresh/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['dup_check'].get('version'), 3)
        self.assertTrue(r.data['dup_check'].get('ranking'))
