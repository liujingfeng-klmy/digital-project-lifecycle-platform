"""把老的单份评审扫描件搬进 ReviewFile。

改造前每个项目只存一份 review_scan。不搬的话，老项目详情页的
「评审会材料」会是空的，看着像从来没上传过 —— 判定依据凭空消失，
比不分份更糟。文件本身不复制，只把路径指过去。
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Project = apps.get_model('project', 'Project')
    ReviewFile = apps.get_model('project', 'ReviewFile')

    for p in Project.objects.exclude(review_scan='').exclude(review_scan=None):
        if ReviewFile.objects.filter(project_id=p.pk).exists():
            continue
        ReviewFile.objects.create(
            project_id=p.pk,
            file=p.review_scan.name if hasattr(p.review_scan, 'name') else str(p.review_scan),
            name=p.review_scan_name or str(p.review_scan).rsplit('/', 1)[-1],
            kind='summary',
            uploaded_by_id=None,
        )


def backwards(apps, schema_editor):
    # 回滚只是把新表清空，原 review_scan 字段一直没删，不会丢东西
    apps.get_model('project', 'ReviewFile').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('project', '0010_supplementnotice_kind_alter_project_review_agree_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
