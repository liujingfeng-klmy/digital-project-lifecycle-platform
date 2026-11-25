from django.urls import path

from . import views

urlpatterns = [
    path('llm/presets', views.PresetsView.as_view(), name='llm-presets'),
    path('llm/config', views.ConfigView.as_view(), name='llm-config'),
    path('llm/test', views.TestView.as_view(), name='llm-test'),
]
