from django.urls import path
from django.views.generic import TemplateView

from . import views

app_name = 'explorer'
urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('queue', views.QueueView.as_view(template_name='explorer/queue.html'), name='queue'),
    path('history', TemplateView.as_view(template_name='explorer/history.html'), name='history'),
]
