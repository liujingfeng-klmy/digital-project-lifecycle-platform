"""全局分页类。

DRF 的 PageNumberPagination 把 page_size_query_param / max_page_size
硬编码为 None，且 api_settings 里根本没有这两个键 —— 在 settings.py 的
REST_FRAMEWORK 字典里写它们不会生效。前端一直在传 page_size=100/200
却只拿到 20 条，根因就在这里。用子类把类属性补齐，分页上限统一收口。
"""
from rest_framework.pagination import PageNumberPagination


class ApiPageNumberPagination(PageNumberPagination):
    """允许客户端用 ?page_size=N 调整每页条数，封顶 200。

    page_size=200 的设置页（角色/模板）与 page_size=100 的资料页都能
    一次取全，同时防止超大 page_size 把服务拖垮。
    """

    page_size_query_param = 'page_size'
    max_page_size = 200
