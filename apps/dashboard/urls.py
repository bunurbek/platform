from django.urls import path
from . import views

urlpatterns = [
    path('',                            views.dashboard_home,    name='dashboard_home'),
    path('students/',                   views.students,          name='dash_students'),
    path('students/<uuid:pk>/',         views.student_detail,    name='dash_student_detail'),
    path('students/<uuid:pk>/enroll/',  views.enroll_student,    name='dash_enroll'),
    path('students/<uuid:pk>/revoke/',  views.revoke_student,    name='dash_revoke'),
    path('payments/',                   views.payments,          name='dash_payments'),
    path('content/',                    views.content,           name='dash_content'),
    # Module CRUD
    path('content/module/add/',                views.module_add,         name='dash_module_add'),
    path('content/module/<int:pk>/toggle-free/', views.module_toggle_free, name='dash_module_toggle'),
    path('content/module/<int:pk>/delete/',    views.module_delete,      name='dash_module_delete'),
    # Lesson CRUD (within module)
    path('content/module/<int:module_pk>/lesson/add/', views.lesson_add, name='dash_lesson_add'),
    path('content/lesson/<int:pk>/delete/',    views.lesson_delete,      name='dash_lesson_delete'),
    path('content/lesson/<int:pk>/update/',    views.lesson_update,      name='dash_lesson_update'),
    path('analytics/',                  views.analytics,         name='dash_analytics'),
]
