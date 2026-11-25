import re

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Role, User


class RoleSerializer(serializers.ModelSerializer):
    scope_label = serializers.CharField(read_only=True)
    user_count = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ['id', 'code', 'name', 'scope', 'scope_label', 'description',
                  'builtin', 'active', 'order', 'user_count', 'created']
        read_only_fields = ['code', 'builtin', 'created']

    def get_user_count(self, obj):
        return obj.users.filter(is_active=True).count()


class UserSerializer(serializers.ModelSerializer):
    """账号输出。

    role 给的是权限域（applicant/reviewer/auditor/admin），前端按域决定
    能进哪些页面；role_label 是具体角色名（可以是「初审岗（数据）」这类
    自定义角色），role_id 供编辑时回填下拉。
    """

    role = serializers.CharField(read_only=True)
    role_id = serializers.IntegerField(source='role_ref_id', read_only=True)
    role_code = serializers.CharField(read_only=True)
    role_label = serializers.CharField(read_only=True)
    scope_label = serializers.SerializerMethodField()
    display = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'name', 'display', 'role', 'role_id', 'role_code',
                  'role_label', 'scope_label', 'unit', 'phone', 'is_active',
                  'must_change_password', 'created']
        read_only_fields = ['created']

    def get_scope_label(self, obj):
        return dict(Role.Scope.choices).get(obj.role, '')


class _RoleWriteMixin(serializers.ModelSerializer):
    """建号 / 改号时按角色 ID 写库。

    写接口收的是 role_id（角色表主键），不是角色名 —— 名字可以改，
    主键不会变，改完名字历史账号仍然挂在同一个角色上。
    """

    role_id = serializers.IntegerField(write_only=True, required=False)

    def validate_role_id(self, value):
        if value in (None, ''):
            return None
        if not Role.objects.filter(pk=value, active=True).exists():
            raise serializers.ValidationError('角色不存在或已停用')
        return value

    def _normalize(self, validated_data):
        rid = validated_data.pop('role_id', None)
        if rid:
            validated_data['role_ref_id'] = rid
        return validated_data


class UserUpdateSerializer(_RoleWriteMixin):
    """管理员编辑账号。

    原来只有一个「新建」序列化器，账号建完就改不了：用户名打错一个字母
    （库里真出现过 zhuangjia3）也只能重建账号，历史留痕全断在旧账号上。
    这里放开用户名、姓名、角色、单位、手机与启停，**不含密码**——重置口令
    是单独的动作，要连带把旧会话踢掉，不能和普通字段一起悄悄改。
    """

    class Meta:
        model = User
        fields = ['username', 'name', 'role_id', 'unit', 'phone', 'is_active']

    def validate_username(self, value):
        value = (value or '').strip()
        if len(value) < 3:
            raise serializers.ValidationError('用户名至少 3 个字符')
        if not re.match(r'^[A-Za-z0-9_.-]+$', value):
            raise serializers.ValidationError('用户名只能用字母、数字、下划线、点和短横线')
        return value

    def update(self, instance, validated_data):
        validated_data = self._normalize(validated_data)
        return super().update(instance, validated_data)


class ResetPasswordSerializer(serializers.Serializer):
    """管理员重置口令。改完强制对方下次登录自己再改一次。"""

    password = serializers.CharField(min_length=8)

    def validate_password(self, value):
        user = self.context['user']
        try:
            validate_password(value, user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        if user.check_password(value):
            raise serializers.ValidationError('新密码不能与原密码相同')
        return value


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={'input_type': 'password'})

    def validate(self, attrs):
        user = authenticate(username=attrs['username'].strip(),
                            password=attrs['password'])
        if not user:
            raise serializers.ValidationError('用户名或密码不正确')
        if not user.is_active:
            raise serializers.ValidationError('该账号已停用，请联系管理员')
        # 「停用角色」= 收回权限：其下账号一律不得登录，即使账号本身 is_active。
        # 角色是权限的真实来源（user.role 读的就是 role_ref.scope），只停在
        # 账号层会漏掉「角色停用但账号没停用」的情况。
        if user.role_ref_id and not user.role_ref.active:
            raise serializers.ValidationError('该角色已停用，请联系管理员')
        attrs['user'] = user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField(min_length=8)

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('原密码不正确')
        return value

    def validate_new_password(self, value):
        """走 Django 全套口令校验，并禁止「改成和原来一样」。

        原来只靠 min_length=8，`12345678` 也能过；把默认口令原样再输一遍
        同样能过 —— 等于强制改密根本没生效。
        """
        user = self.context['request'].user
        if user.check_password(value):
            raise serializers.ValidationError('新密码不能与原密码相同')
        try:
            validate_password(value, user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.must_change_password = False
        # 版本 +1：此前签发的 token 全部作废，别的设备上的会话立即失效
        user.token_version = (user.token_version or 0) + 1
        user.save(update_fields=['password', 'must_change_password', 'token_version'])
        return user


class UserCreateSerializer(_RoleWriteMixin):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ['id', 'username', 'name', 'role_id', 'unit', 'phone',
                  'password', 'is_active']

    def validate_password(self, value):
        # 原来只有 min_length=8，12345678 也能过。建号与改密/重置走同一套
        # Django 口令校验器，弱口令在入口就被挡住。
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def validate(self, attrs):
        if not attrs.get('role_id'):
            raise serializers.ValidationError({'role_id': '请选择角色'})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        validated_data = self._normalize(validated_data)
        user = User(**validated_data)
        user.set_password(password)
        user.must_change_password = True
        user.save()
        return user
