"""后台维护入口。

申报单位名录的增删改放在这里：机构改革时加一个单位、停用撤并的单位，
不必等版本更新。停用（active=False）比删除安全 —— 历史项目里存的是单位
名称字符串，删掉名录不影响台账，但下拉里不该再出现。
"""
from django.contrib import admin

from .models import OrgUnit, Role


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'scope', 'builtin', 'active', 'order')
    list_filter = ('scope', 'active', 'builtin')
    list_editable = ('order', 'active')
    search_fields = ('name', 'code', 'description')


@admin.register(OrgUnit)
class OrgUnitAdmin(admin.ModelAdmin):
    list_display = ('name', 'group', 'subgroup', 'order', 'active')
    list_filter = ('group', 'active')
    list_editable = ('order', 'active')
    search_fields = ('name', 'subgroup')
    ordering = ('order', 'id')
