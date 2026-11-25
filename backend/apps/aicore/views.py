from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsAdminRole

from . import llm as llm_mod


class PresetsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'presets': llm_mod.PRESETS})


class ConfigView(APIView):
    """模型配置。读取对所有登录用户开放（密钥掩码），修改仅管理员。"""

    def get_permissions(self):
        if self.request.method in ('PUT', 'PATCH'):
            return [IsAdminRole()]
        return [IsAuthenticated()]

    def get(self, request):
        return Response(llm_mod.public_config())

    def put(self, request):
        payload = request.data or {}
        llm_patch = dict(payload.get('llm') or {})

        incoming_key = llm_patch.get('api_key')
        if incoming_key is not None and '****' in str(incoming_key):
            llm_patch.pop('api_key')

        allowed = {'provider', 'base_url', 'model', 'api_key', 'temperature',
                   'max_tokens', 'timeout', 'chunk_chars', 'enabled'}
        llm_patch = {k: v for k, v in llm_patch.items() if k in allowed}

        # 地址必须是公网 http(s)，否则管理员误配或被钓鱼时，模型地址可指向
        # 回环/内网/云元数据，测试连接会把已存 Key 一起发出去（SSRF + 密钥外泄）
        if 'base_url' in llm_patch:
            try:
                llm_patch['base_url'] = llm_mod.validate_base_url(llm_patch['base_url'])
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=400)

        if 'temperature' in llm_patch:
            try:
                llm_patch['temperature'] = max(0.0, min(1.5, float(llm_patch['temperature'])))
            except (TypeError, ValueError):
                llm_patch.pop('temperature')

        patch = {'llm': llm_patch}

        for section in ('review', 'flow', 'sensitive'):
            if isinstance(payload.get(section), dict):
                patch[section] = payload[section]

        llm_mod.save_config(patch)
        return Response(llm_mod.public_config())

    def patch(self, request):
        return self.put(request)


class TestView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        cfg = llm_mod.get_llm()
        base_url = (request.data.get('base_url') or cfg.get('base_url') or '').strip()
        model = (request.data.get('model') or cfg.get('model') or '').strip()
        api_key = request.data.get('api_key')
        if api_key is None or '****' in str(api_key):
            api_key = cfg.get('api_key', '')

        # 测试连接同样要挡 SSRF：临时传入的 base_url 可能指向内网/元数据地址，
        # 此时不能把已存 Key 发出去。
        try:
            base_url = llm_mod.validate_base_url(base_url)
        except ValueError as exc:
            return Response({'ok': False, 'error': str(exc)}, status=400)

        result = llm_mod.test_connection(base_url, model, api_key)
        code = status.HTTP_200_OK if result.get('ok') else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)
