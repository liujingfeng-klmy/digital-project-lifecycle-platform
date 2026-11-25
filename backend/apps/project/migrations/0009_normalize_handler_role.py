"""把历史项目的「当前办理角色」按阶段重算一遍。

评审专家角色取消后，停在评审阶段的老项目 handler_role 还写着 'expert'，
这个值在角色表里已经不存在了：界面显示成「当前办理：已结束」，
按角色统计与导出的那一列也成了英文裸值。
"""
from django.db import migrations


def forward(apps, schema_editor):
    Project = apps.get_model('project', 'Project')
    # 与 flow.STAGE_ROLE 保持一致（迁移里不 import 业务模块，避免以后改动把历史迁移带崩）
    stage_role = {
        'precheck': 'reviewer',
        'supplement': 'applicant',
        'expert_review': 'reviewer',
        'audit': 'auditor',
        'tendering': 'reviewer',
        'building': 'reviewer',
        'trial': 'reviewer',
        'acceptance': 'auditor',
        'draft': 'applicant',
        'accepted': '',
        'rejected': '',
    }
    for stage, role in stage_role.items():
        Project.objects.filter(stage=stage).exclude(handler_role=role).update(
            handler_role=role)


def backward(apps, schema_editor):
    """回不去也无所谓：旧值 'expert' 在新角色表里已经没有意义，
    真要回滚到旧版本，重跑一次旧代码的流转就会重新写入。"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('project', '0008_remove_project_experts_remove_project_leader_and_more'),
    ]

    operations = [migrations.RunPython(forward, backward)]
