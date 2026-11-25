from rest_framework import serializers

from .models import ArchiveFile, ArchiveProject, CompareReview


class ArchiveFileBriefSerializer(serializers.ModelSerializer):
    doc_type_label = serializers.CharField(source='get_doc_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    size_mb = serializers.SerializerMethodField()

    class Meta:
        model = ArchiveFile
        fields = ['id', 'filename', 'doc_type', 'doc_type_label', 'size', 'size_mb',
                  'status', 'status_label', 'error', 'char_count', 'page_count',
                  'created']

    def get_size_mb(self, obj):
        return round((obj.size or 0) / 1048576, 2)


class ArchiveProjectSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    files = ArchiveFileBriefSerializer(many=True, read_only=True)
    file_count = serializers.SerializerMethodField()
    char_total = serializers.SerializerMethodField()
    ready_count = serializers.SerializerMethodField()

    class Meta:
        model = ArchiveProject
        fields = ['id', 'name', 'unit', 'year', 'year_source', 'investment', 'category',
                  'status', 'status_label', 'note', 'files', 'file_count',
                  'ready_count', 'char_total', 'created', 'updated']

    def get_file_count(self, obj):
        return len(obj.files.all())

    def get_ready_count(self, obj):
        return sum(1 for f in obj.files.all() if f.status == ArchiveFile.Status.READY)

    def get_char_total(self, obj):
        return sum(f.char_count or 0 for f in obj.files.all())


class ArchiveProjectWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ArchiveProject
        fields = ['name', 'unit', 'year', 'investment', 'category', 'status', 'note']

    def validate_name(self, v):
        v = (v or '').strip()
        if len(v) < 2:
            raise serializers.ValidationError('项目名称至少 2 个字')
        return v


class ArchiveFileDetailSerializer(serializers.ModelSerializer):
    doc_type_label = serializers.CharField(source='get_doc_type_display', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    sections = serializers.SerializerMethodField()
    functions = serializers.SerializerMethodField()

    class Meta:
        model = ArchiveFile
        fields = ['id', 'project', 'project_name', 'filename', 'doc_type', 'doc_type_label',
                  'size', 'status', 'error', 'char_count', 'page_count', 'sections',
                  'functions', 'created']

    def get_sections(self, obj):
        # 章节正文可能上万字，详情只给标题与首句；全文另走 /text 接口
        from .textparse import CATEGORIES
        out = []
        for s in (obj.sections or []):
            body = s.get('text') or ''
            out.append({
                'title': s.get('title', ''),
                'category': s.get('category', 'other'),
                'category_label': s.get('category_label') or CATEGORIES.get(s.get('category'), '其他'),
                'chars': len(body),
                'preview': body[:260],
            })
        return out

    def get_functions(self, obj):
        """抽出来的功能项要给用户看 —— 抽错了才能发现，不然只能看到一个分数。"""
        return [{'name': f.get('name', ''), 'generic': bool(f.get('generic')),
                 'section': f.get('section', '')}
                for f in (obj.functions or [])]


class CompareReviewSerializer(serializers.ModelSerializer):
    """核定记录的读接口。"""
    verdict_label = serializers.CharField(source='get_verdict_display', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)
    reviewer_display = serializers.SerializerMethodField()

    class Meta:
        model = CompareReview
        fields = ['id', 'query_hash', 'query_name', 'query_unit', 'query_year',
                  'project', 'project_name', 'findings', 'verdict', 'verdict_label',
                  'basis', 'reviewer', 'reviewer_display', 'created']
        read_only_fields = ['reviewer', 'created']

    def get_reviewer_display(self, obj):
        if obj.reviewer_name:
            return obj.reviewer_name
        return getattr(obj.reviewer, 'name', '') or getattr(obj.reviewer, 'username', '')


class CompareReviewWriteSerializer(serializers.Serializer):
    """提交核定。字段单独写，避免前端传什么就存什么。"""

    query_hash = serializers.CharField(max_length=64)
    query_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    query_unit = serializers.CharField(max_length=120, required=False, allow_blank=True)
    query_year = serializers.IntegerField(required=False, allow_null=True)
    project = serializers.IntegerField()
    verdict = serializers.ChoiceField(choices=CompareReview.Verdict.choices)
    basis = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    findings = serializers.ListField(child=serializers.DictField(), required=False)

    def validate_project(self, v):
        if not ArchiveProject.objects.filter(pk=v).exists():
            raise serializers.ValidationError('比对的既有项目不存在')
        return v

    def validate(self, attrs):
        # 「构成重复」和「不构成重复」都必须写依据 —— 三个字「已核实」不算
        if attrs['verdict'] in (CompareReview.Verdict.DUPLICATE,
                                CompareReview.Verdict.NOT_DUPLICATE):
            if len((attrs.get('basis') or '').strip()) < 10:
                raise serializers.ValidationError(
                    {'basis': '核定依据至少 10 个字，需写明比对了哪几份材料、看了哪些功能，'
                              '否则将来审计无法追溯。'})
        return attrs
