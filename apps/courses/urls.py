from django.urls import path
from . import views

urlpatterns = [
    path('', views.course_home, name='course_home'),
    path('bepul-dars/', views.free_lesson, name='free_lesson'),
    path('dars/<int:pk>/', views.lesson_view, name='lesson'),
    path('dars/<int:pk>/complete/', views.mark_complete, name='mark_complete'),
]
