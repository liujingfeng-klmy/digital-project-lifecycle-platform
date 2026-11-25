"""补齐存量材料的轮次。

0006 只排了版本号，round 一律是 0；项目已经补正到第 2 轮的，
新版材料仍然显示「第 0 轮」，比版本号还容易看糊涂。
按「同类型最后一版属于当前轮、往前依次递减」回填。
"""
from django.db import migrations


def fix_rounds(apps, schema_editor):
    Project = apps.get_model('project', 'Project')
    ProjectFile = apps.get_model('project', 'ProjectFile')
    for p in Project.objects.all():
        groups = {}
        for f in ProjectFile.objects.filter(project_id=p.id).order_by('id'):
            groups.setdefault(f.doc_type, []).append(f)
        for files in groups.values():
            n = len(files)
            for idx, f in enumerate(files):
                back = n - 1 - idx
                f.round = max((p.round or 0) - back, 0)
                f.save(update_fields=['round'])


class Migration(migrations.Migration):

    dependencies = [
        ('project', '0006_file_versions'),
    ]

    operations = [
        migrations.RunPython(fix_rounds, migrations.RunPython.noop),
    ]
