"""申报阶段三项能力：单位名录、材料预解析、缺失字段提示。

三条都要能过「申报人自己走一遍」的路径：
1. 申报单位只能从市本级名录里选，俗写能归一；
2. 先传材料再填表，抽出来的字段直接进表单；
3. 抽不到的字段要说清楚缺什么、去模板哪一节补，且不能把材料里的单位
   猜成名录里的另一家。
"""
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.core.models import OrgUnit, User

from . import flow, prefill
from .models import Project, ProjectFile

SAMPLE_DIR = Path(__file__).resolve().parents[3] / '样例数据'
SAMPLE = SAMPLE_DIR / '01_市不动产登记信息平台_可行性研究报告.docx'


def make_client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


class OrgUnitDictTest(TestCase):
    def test_seed_loaded_and_grouped(self):
        resp = make_client(User.objects.create_user('u1', password='Tmp#12345')).get(
            '/api/org-units/')
        self.assertEqual(resp.status_code, 200)
        groups = resp.data['groups']
        self.assertGreaterEqual(len(groups), 4)
        labels = [g['label'] for g in groups]
        for must in ('市委部门', '市人大常委会', '市政府部门', '市政协常委会'):
            self.assertIn(must, labels)
        # 数量跟着种子文件走，不写死：名录属业务数据，部署时按本地口径替换
        from apps.core.org_data import ORG_UNITS
        self.assertEqual(resp.data['count'], len(ORG_UNITS))
        self.assertGreaterEqual(resp.data['count'], 50)

    def test_normalize_legacy_writing(self):
        from apps.core import org_units

        self.assertEqual(org_units.normalize('市财政局'), ('财政局', True))
        self.assertEqual(org_units.normalize('市人民代表大会常务委员会'),
                         ('市人大常委会', True))
        self.assertEqual(org_units.normalize('市自然资源和规划局')[1], False)


class TextCleanTest(TestCase):
    """正文清洗：别把标签和小标题当成内容抽进字段。

    真实材料里这两处都踩过：名称抽成「项目名称：“一网统管”…」，
    建设必要性末尾挂着下一节标题「建设紧迫性」。
    """

    def test_name_label_dropped_across_lines(self):
        from apps.archive import extract as archive_extract

        text = ('项目概述\n项目名称\n项目名称：“一网统管”应用场景能力提升项目\n'
                '购买主体及责任人\n购买主体：市数据资源和政务服务中心\n')
        meta = archive_extract.extract(text, '')
        self.assertEqual(meta['name'], '“一网统管”应用场景能力提升项目')

    def test_name_label_dropped_in_prefill(self):
        sections = [{'title': '项目名称', 'text': '项目名称：不动产登记一网通办平台\n申报单位：自然资源局'}]
        name, _src = prefill._guess_name('', sections, '')
        self.assertEqual(name, '不动产登记一网通办平台')

    def test_necessity_trailing_heading_removed(self):
        sections = [{'title': '项目建设的必要性', 'text':
                     '一是打破数据壁垒。当前城市管理存在跨部门数据孤岛，事件处置效率低下。\n'
                     '二是提升治理能力。\n'
                     '建设紧迫性'}]
        body, _src = prefill._guess_necessity(sections)
        self.assertTrue(body.endswith('。'), body)
        self.assertNotIn('建设紧迫性', body)

    def test_stray_heading_detection_keeps_sentences(self):
        # 标题该删
        self.assertTrue(prefill._is_stray_heading('建设紧迫性'))
        self.assertTrue(prefill._is_stray_heading('项目背景'))
        # 正文句子不该删
        self.assertFalse(prefill._is_stray_heading('本项目十分必要'))
        self.assertFalse(prefill._is_stray_heading('为提升治理能力，建设本平台。'))

    def test_category_not_hijacked_by_repeated_word(self):
        """信访场景提了 8 次，不代表整个项目是公安政法类。"""
        cat, _ = prefill._guess_category(
            summary='建设一网统管指挥大屏，覆盖建筑垃圾清运、信访、市场监管等场景，'
                    '提升城市管理与社会治理水平。',
            necessity='信访事项需跨部门流转。信访件办理时限长。信访数据分析缺失。',
            name='“一网统管”应用场景能力提升项目')
        self.assertEqual(cat, '城市管理')


class PrefillApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.applicant = User.objects.create_user('pf_user', password='Tmp#12345',
                                                 role=User.Role.APPLICANT)
        cls.reviewer = User.objects.create_user('pf_rev', password='Tmp#12345',
                                                role=User.Role.REVIEWER)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        override = override_settings(MEDIA_ROOT=Path(self.tmp.name))
        override.enable()
        self.addCleanup(override.disable)
        patcher = mock.patch.object(prefill, 'ai_extract', return_value=({}, '测试环境不调模型'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self, name='01_市不动产登记信息平台_可行性研究报告.docx'):
        with open(SAMPLE, 'rb') as fh:
            return SimpleUploadedFile(name, fh.read(),
                                      content_type='application/vnd.openxmlformats-'
                                                   'officedocument.wordprocessingml.document')

    def test_prefill_extracts_core_fields(self):
        resp = make_client(self.applicant).post(
            '/api/projects/prefill/', {'file': self.upload()}, format='multipart')
        self.assertEqual(resp.status_code, 200, resp.data)
        fields = resp.data['fields']
        self.assertEqual(fields['name'], '市不动产登记信息平台')
        self.assertEqual(fields['investment'], 820.0)
        self.assertEqual(fields['duration_months'], 18)
        self.assertTrue(resp.data['token'])

    def test_prefill_reports_missing_with_template_hint(self):
        resp = make_client(self.applicant).post(
            '/api/projects/prefill/', {'file': self.upload()}, format='multipart')
        self.assertEqual(resp.status_code, 200, resp.data)
        missing = {m['key']: m for m in resp.data['missing']}
        # 样例材料的必要性写在小节里，规则抽不到由模型补；测试禁用了模型，
        # 所以这里应当如实报缺，并给出模板位置
        self.assertIn('necessity', missing)
        self.assertIn('第 1.3.1 节', missing['necessity']['hint'])

    def test_prefill_unit_not_in_dict_is_flagged(self):
        resp = make_client(self.applicant).post(
            '/api/projects/prefill/', {'file': self.upload()}, format='multipart')
        # 样例写的是「市自然资源和规划局」，名录里是「自然资源局」
        self.assertTrue(resp.data['unit_unmatched'])
        self.assertEqual(resp.data['unit_raw'], '市自然资源和规划局')

    def test_create_with_token_attaches_material(self):
        client = make_client(self.applicant)
        pre = client.post('/api/projects/prefill/', {'file': self.upload()},
                          format='multipart')
        self.assertEqual(pre.status_code, 200, pre.data)

        resp = client.post('/api/projects/', {
            'name': '市不动产登记信息平台升级改造',
            'unit': '自然资源局',
            'year': 2026,
            'investment': '820.00',
            'summary': '建设不动产登记业务办理子系统，覆盖受理、审核、登簿、发证全流程。',
            'prefill_token': pre.data['token'],
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data['attach']['attached'], resp.data['attach'])

        project = Project.objects.get(pk=resp.data['id'])
        file = project.files.get()
        self.assertEqual(file.status, ProjectFile.Status.READY)
        self.assertGreater(file.char_count, 1000)

    def test_create_rejects_unit_outside_dict(self):
        resp = make_client(self.applicant).post('/api/projects/', {
            'name': '单位不在名录的项目',
            'unit': '不存在的某某局',
            'investment': '100.00',
            'summary': '说明文字需要超过三十个字，否则预审没有对照依据可看。',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('名录', str(resp.data))

    def test_create_normalizes_legacy_unit(self):
        resp = make_client(self.applicant).post('/api/projects/', {
            'name': '俗写单位归一测试项目',
            'unit': '市财政局',
            'investment': '100.00',
            'summary': '说明文字需要超过三十个字，否则预审没有对照依据可看。',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['unit'], '财政局')

    def test_reviewer_cannot_prefill(self):
        resp = make_client(self.reviewer).post(
            '/api/projects/prefill/', {'file': self.upload()}, format='multipart')
        self.assertEqual(resp.status_code, 403)

    def test_submit_blocks_unknown_unit(self):
        """历史项目里存了名录外的单位时，提交必须拦住并说清怎么改。"""
        project = Project.objects.create(code='XM2026-777', name='历史项目申报',
                                         unit='市行政审批局', applicant=self.applicant,
                                         investment=100, summary='建设内容' * 20)
        with self.assertRaises(flow.FlowError) as ctx:
            flow.transition(project, self.applicant, 'submit', {})
        self.assertIn('名录', str(ctx.exception))

    def test_template_download(self):
        resp = make_client(self.applicant).get('/api/projects/feasibility-template/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'],
                         'application/vnd.openxmlformats-officedocument.'
                         'wordprocessingml.document')
