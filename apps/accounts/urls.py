from django.urls import path
from . import views

urlpatterns = [
    path('start/',                  views.tg_start,    name='tg_start_view'),
    path('start/waiting/',          views.tg_waiting,  name='tg_waiting'),
    path('start/status/',           views.tg_status,   name='tg_status'),
    path('start/verify/',           views.tg_verify,   name='tg_verify'),
    path('login/',                  views.login_view,  name='login'),
    path('logout/',                 views.logout_view, name='logout'),
    path('profile/',                views.profile_view, name='profile'),
]
