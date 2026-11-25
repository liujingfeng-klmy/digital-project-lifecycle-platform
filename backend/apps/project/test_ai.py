# -*- coding: utf-8 -*-
"""AI 预审的防线测试。

专门钉住两个出过的问题：
1) 模型把额度花在思考上、正文是空的，被当成「这章没问题」，报告直接判通过；
2) 某章解析失败，整份报告仍给出「通过」结论。
"""
from unittest.mock import patch

from django.test import TestCase

from apps.archive import textparse
from apps.core.models import User

from . import precheck
from .models import PrecheckReport, Project, ProjectFile


class AIPrecheckTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.applicant = User.objects.create(
            username='ai_tester', role=User.Role.APPLICANT)
        cls.p = Project.objects.create(
            name='测试项目', unit='测试单位', year=2026, category='测试',
            investment=500, applicant=cls.applicant)
        ProjectFile.objects.create(
            project=cls.p, filename='测试可研.docx', doc_type='feasibility',
            status=ProjectFile.Status.READY, text='正文' * 200,
            chapters=[
                {'title': '第一章 项目概述', 'category': 'necessity',
                 'text': '现状描述。' * 120},
                {'title': '第七章 投资估算', 'category': 'invest',
                 'text': '投资总额五百万元。' * 60},
            ],
            sections=[],
        )

    def _cfg(self):
        return {'llm': {'enabled': True, 'base_url': 'http://fake/v1',
                        'model': 'fake', 'max_tokens': 8000,
                        'temperature': 0.1, 'timeout': 30}}

    def test_findings_parser(self):
        self.assertFalse(precheck._parse_findings('')['ok'])
        empty = precheck._parse_findings('[]')
        self.assertTrue(empty['ok'])
        self.assertEqual(empty['items'], [])

        # 中文等级要映射回三档，不能一律当 warn
        r = precheck._parse_findings('[{"level":"高","title":"投资无依据"}]')
        self.assertTrue(r['ok'])
        self.assertEqual(r['items'][0]['level'], 'block')
        self.assertEqual(precheck._norm_level('中'), 'warn')
        self.assertEqual(precheck._norm_level('低'), 'info')

        # 被截断时，前面完整的条目要抢救出来
        r = precheck._parse_findings(
            '[{"level":"block","title":"甲"},{"level":"block","title":"乙"},{"le')
        self.assertTrue(r['ok'])
        self.assertEqual([i['title'] for i in r['items']], ['甲', '乙'])

        # 围栏和前言要剥掉
        r = precheck._parse_findings('结果如下：\n```json\n[{"level":"info","title":"丙"}]\n```')
        self.assertTrue(r['ok'])
        self.assertEqual(r['items'][0]['title'], '丙')

    def test_all_blank_raises_and_writes_no_report(self):
        with patch('apps.aicore.llm.load_config', return_value=self._cfg()), \
             patch('apps.aicore.llm.chat',
                   return_value=('', {'finish_reason': 'length',
                                      'reasoning_tokens': 8000})):
            with self.assertRaises(RuntimeError):
                precheck.run_ai_review(self.p, self.applicant)

        self.assertEqual(PrecheckReport.objects.filter(project=self.p).count(), 0)

    def test_partial_blank_cannot_pass(self):
        good = '[{"level":"info","title":"表述可优化","quote":"","issue":"说明","advice":"建议"}]'
        # 第一章被截断（截断不追问），第二章正常
        replies = [('', {'finish_reason': 'length'}),
                   (good, {'finish_reason': 'stop', 'total_tokens': 100})]

        with patch('apps.aicore.llm.load_config', return_value=self._cfg()), \
             patch('apps.aicore.llm.chat', side_effect=replies):
            rep = precheck.run_ai_review(self.p, self.applicant)

        self.assertEqual(rep.counts.get('unread'), 1)
        self.assertNotEqual(rep.verdict, PrecheckReport.Verdict.PASS,
                            '有章节没审成，不能判通过')
        self.assertIn('未取得有效结果', rep.note)


class ChapterSplitTest(TestCase):
    def test_subsection_merges_into_chapter(self):
        text = ('第一章 项目概述\n1.1 背景\n现有系统建成较早，功能覆盖不足。\n'
                '1.2 必要性\n落实上级部署要求，提升服务效能。\n'
                '第二章 需求分析\n2.1 业务需求\n需要支持登记与核定等业务。')
        chapters = textparse.split_chapters(text)
        self.assertEqual([c['title'] for c in chapters],
                         ['第一章 项目概述', '第二章 需求分析'])
        # 小节标题保留在正文里，方便模型引用时定位
        self.assertIn('1.1 背景', chapters[0]['text'])
        self.assertIn('1.2 必要性', chapters[0]['text'])
        self.assertEqual(len(chapters[0]['parts']), 2)
