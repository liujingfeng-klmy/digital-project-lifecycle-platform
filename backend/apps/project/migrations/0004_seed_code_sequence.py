"""把已有项目编号灌进序列，避免上线后新项目从 001 重新发号撞车。

同时把序列推到「历史最大号」，删过的号不再回收。
"""
from django.db import migrations


def seed_sequences(apps, schema_editor):
    Project = apps.get_model('project', 'Project')
    Sequence = apps.get_model('project', 'ProjectCodeSequence')

    best = {}
    for code in Project.objects.values_list('code', flat=True):
        if not code or not code.startswith('XM') or '-' not in code:
            continue
        head, _, tail = code.partition('-')
        try:
            year, no = int(head[2:]), int(tail)
        except ValueError:
            continue
        best[year] = max(best.get(year, 0), no)

    for year, no in best.items():
        Sequence.objects.update_or_create(year=year, defaults={'last_no': no})


def unseed(apps, schema_editor):
    # 回滚不清序列：序列只增不回退，清掉会让号码被复用
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('project', '0003_projectcodesequence_project_leader_project_sensitive'),
    ]

    operations = [
        migrations.RunPython(seed_sequences, unseed),
    ]
