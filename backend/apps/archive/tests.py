"""历史档案库的回归测试（此前这个 app 一个测试都没有）。

覆盖：导入失败的占位项目清理、核定台账的删除保护、档案原文的访问口径。
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from rest_framework.test import APIClient

from apps.core.models import User

from . import services
from .models import ArchiveFile, ArchiveProject, CompareReview


class ArchiveBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user('a_admin', password='Pw@12345678',
                                              role='admin')
        self.reviewer = User.objects.create_user('a_rev', password='Pw@12345678',
                                                 role='reviewer')
        self.applicant = User.objects.create_user('a_app', password='Pw@12345678',
                                                  role='applicant', unit='市某局')


class ImportPlaceholderTest(ArchiveBase):
    """问题 18：解析失败不留「（待归入）」占位项目。"""

    def test_failed_import_leaves_nothing_behind(self):
        upload = SimpleUploadedFile('坏文件.pdf', b'not a real pdf',
                                    content_type='application/pdf')

        def fake_parse(rec, year_hint=None):
            rec.error = '只解析出 3 个字符'
            rec.save(update_fields=['error'])
            return rec, None

        with patch.object(services, 'parse_into_record', side_effect=fake_parse):
            result = services.ingest_upload(upload, self.reviewer)

        self.assertFalse(result['ok'])
        self.assertEqual(result['message'], '只解析出 3 个字符')
        self.assertEqual(ArchiveProject.objects.filter(name='（待归入）').count(), 0)
        self.assertEqual(ArchiveFile.objects.count(), 0)
        self.assertEqual(ArchiveProject.objects.count(), 0)

    def test_failed_import_into_existing_project_keeps_project(self):
        target = ArchiveProject.objects.create(name='既有历史项目')
        upload = SimpleUploadedFile('坏文件2.pdf', b'broken',
                                    content_type='application/pdf')

        def fake_parse(rec, year_hint=None):
            rec.error = '解析失败'
            rec.save(update_fields=['error'])
            return rec, None

        with patch.object(services, 'parse_into_record', side_effect=fake_parse):
            result = services.ingest_upload(upload, self.reviewer, project=target)

        self.assertFalse(result['ok'])
        self.assertTrue(ArchiveProject.objects.filter(pk=target.pk).exists())
        self.assertEqual(ArchiveFile.objects.count(), 0)


class CompareReviewProtectTest(ArchiveBase):
    """问题 13：核定台账不能被连带删除。"""

    def test_project_with_review_cannot_be_deleted(self):
        ap = ArchiveProject.objects.create(name='被核定过的项目')
        CompareReview.objects.create(query_hash='h' * 64, project=ap,
                                     verdict='duplicate', basis='功能高度重合')
        with self.assertRaises(ProtectedError):
            ap.delete()
        self.assertTrue(ArchiveProject.objects.filter(pk=ap.pk).exists())

    def test_api_returns_400_instead_of_500(self):
        ap = ArchiveProject.objects.create(name='被核定过的项目2')
        CompareReview.objects.create(query_hash='i' * 64, project=ap,
                                     verdict='duplicate')
        c = APIClient()
        # 删除/合并档案已收敛到管理员（消除"能删不能看"的权限倒挂）；
        # 用管理员验证业务校验：被核定过的档案返回 400 而不是 500
        c.force_authenticate(self.admin)
        resp = c.delete(f'/api/archive/projects/{ap.id}/')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('核定', resp.data['detail'])

    def test_reviewer_cannot_delete_archive(self):
        """审核岗不能再删审计档案（原来能删却看不到内容）。"""
        ap = ArchiveProject.objects.create(name='不可删项目')
        c = APIClient()
        c.force_authenticate(self.reviewer)
        resp = c.delete(f'/api/archive/projects/{ap.id}/')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(ArchiveProject.objects.filter(pk=ap.pk).exists())

    def test_merge_blocked_when_source_has_review(self):
        target = ArchiveProject.objects.create(name='目标项目')
        source = ArchiveProject.objects.create(name='待合并项目')
        CompareReview.objects.create(query_hash='j' * 64, project=source,
                                     verdict='duplicate')
        c = APIClient()
        c.force_authenticate(self.admin)
        resp = c.post('/api/archive/projects/merge/',
                      {'source_ids': [source.id], 'target_id': target.id},
                      format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(ArchiveProject.objects.filter(pk=source.pk).exists())


class ArchiveTextPermissionTest(ArchiveBase):
    """问题 15：历史档案原文只给管理员（2026-11-21 收紧）。

    原来的口径是「业务角色都能读、只挡申报单位」，等于任何业务账号都能把
    历年别家单位的可研翻一遍。判重只需要系统算出的疑点，不需要放开原档。
    """

    def setUp(self):
        super().setUp()
        ap = ArchiveProject.objects.create(name='别家单位的历史项目')
        self.rec = ArchiveFile.objects.create(project=ap, filename='可研.pdf',
                                              text='历史材料正文' * 50,
                                              status=ArchiveFile.Status.READY)

    def test_applicant_forbidden(self):
        c = APIClient()
        c.force_authenticate(self.applicant)
        resp = c.get(f'/api/archive/files/{self.rec.id}/text/')
        self.assertEqual(resp.status_code, 403)

    def test_reviewer_forbidden(self):
        c = APIClient()
        c.force_authenticate(self.reviewer)
        resp = c.get(f'/api/archive/files/{self.rec.id}/text/')
        self.assertEqual(resp.status_code, 403)

    def test_archive_list_admin_only(self):
        c = APIClient()
        c.force_authenticate(self.reviewer)
        self.assertEqual(c.get('/api/archive/projects/').status_code, 403)
        self.assertEqual(c.get('/api/archive/stats/').status_code, 403)
        c.force_authenticate(self.admin)
        self.assertEqual(c.get('/api/archive/projects/').status_code, 200)
        self.assertEqual(c.get('/api/archive/stats/').status_code, 200)

    def test_admin_allowed(self):
        c = APIClient()
        c.force_authenticate(self.admin)
        resp = c.get(f'/api/archive/files/{self.rec.id}/text/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('历史材料正文', resp.data['text'])


class CompareInputTest(ArchiveBase):
    """问题 17：比对接口的非法年度不能打成 500。"""

    def test_bad_year_does_not_crash(self):
        c = APIClient()
        c.force_authenticate(self.reviewer)
        resp = c.post('/api/archive/compare/',
                      {'text': '这是一份用于比对的正文材料。' * 10, 'year': 'abc'},
                      format='json')
        self.assertEqual(resp.status_code, 200)


class YearExtractTest(TestCase):
    """年度识别：封面日期优先于正文高频年份。

    原来的规则是「正文前 2 万字里出现次数最多的 20xx 年」，36 个档案里
    有 18 个取到的是政策依据里的年份。这几条桩就是钉住优先级。
    """

    def test_label_date_beats_frequency(self):
        from . import extract
        text = ('项目名称：某平台建设项目\n申报日期：2023 年 1 月 29 日\n'
                '依据 2019 年文件、2019 年规划，2019 年启动前期工作。' * 3)
        meta = extract.extract(text, '某平台可研.docx')
        self.assertEqual(meta['year'], 2023)
        self.assertEqual(meta['year_source'], 'label')

    def test_filename_year_when_no_label(self):
        from . import extract
        meta = extract.extract('正文没有任何日期，只有建设内容。' * 20,
                               '数字底座项目可研-2022（终版）.docx')
        self.assertEqual(meta['year'], 2022)
        self.assertEqual(meta['year_source'], 'filename')

    def test_frequency_needs_two_hits(self):
        from . import extract
        # 只出现一次，不足以当年度
        text = ('第一章 项目概况\n本项目围绕数据治理展开。' * 3
                + '依据 2017 年印发的文件编制。'
                + '第二章 建设内容\n数据共享交换、安全防护。' * 3)
        meta = extract.extract(text, 'x.docx')
        self.assertIsNone(meta['year'])
        self.assertEqual(meta['year_source'], '')

    def test_hint_wins(self):
        from . import extract
        meta = extract.extract('申报日期：2023 年 1 月', 'x-2019.docx', fallback_year=2026)
        self.assertEqual(meta['year'], 2026)
        self.assertEqual(meta['year_source'], 'hint')


class CompareScopeTest(TestCase):
    """查重只比「建设内容 + 需求分析」两类章。

    政务可研的必要性、政策依据、安全保障、实施计划几乎逐字相同，整篇比
    出来人人高分，真正的重复反而被淹掉 —— 这是 2026-11-19 收窄口径的原因。
    """

    def test_generic_chapters_excluded(self):
        from . import textparse as tp
        for title in ('项目背景', '建设必要性', '投资估算', '实施计划',
                      '等保测评方案', '网络安全方案', '绩效目标'):
            self.assertNotIn(tp.classify(title), tp.COMPARE_CATEGORIES, title)
        for title in ('建设内容', '项目主要建设内容', '需求分析', '功能设计',
                      '技术方案', '系统架构设计'):
            self.assertIn(tp.classify(title), tp.COMPARE_CATEGORIES, title)

    def test_scope_text_only_keeps_compare_chapters(self):
        sections = [
            {'title': '项目背景', 'text': '政策依据与必要性论述。', 'category': 'necessity'},
            {'title': '建设内容', 'text': '统一身份认证、数据共享交换。', 'category': 'plan'},
            {'title': '投资估算', 'text': '工程费 800 万元。', 'category': 'invest'},
            {'title': '需求分析', 'text': '需要支持一网通办。', 'category': 'demand'},
        ]
        text = services.compare_text_of(sections)
        self.assertIn('统一身份认证', text)
        self.assertIn('一网通办', text)
        # 通用章节一个字都不能进比对范围
        self.assertNotIn('政策依据', text)
        self.assertNotIn('工程费', text)

    def test_scope_falls_back_when_no_compare_chapter(self):
        sections = [
            {'title': '项目背景', 'text': '只有必要性。', 'category': 'necessity'},
            {'title': '投资估算', 'text': '工程费 800 万元。', 'category': 'invest'},
        ]
        self.assertEqual(services.compare_text_of(sections).strip(), '')

    def test_retag_fixes_stale_category(self):
        """存量 section 的 category 是入库时存死的，读的时候必须按新规则重算。"""
        from . import textparse as tp
        stale = [{'title': '等保测评方案', 'text': '……', 'category': 'plan'}]
        fixed = tp.retag(stale)
        self.assertNotIn(fixed[0]['category'], tp.COMPARE_CATEGORIES)
