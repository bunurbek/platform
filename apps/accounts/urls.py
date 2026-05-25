from django.urls import path
from . import views

urlpatterns = [
    path('start/',                  views.tg_start,    name='tg_start_view'),
    path('start/waiting/',          views.tg_waiting,  name='tg_waiting'),
    path('start/status/',           views.tg_status,   name='tg_status'),
    path('start/finish/<str:token>/', views.tg_finish, name='tg_finish'),
    path('login/',                  views.login_view,  name='login'),
    path('logout/',                 views.logout_view, name='logout'),
    path('profile/',                views.profile_view, name='profile'),
]
