from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone
from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'full_name', 'phone', 'is_enrolled', 'enrolled_at', 'created_at', 'is_staff')
    list_filter = ('is_enrolled', 'is_staff', 'is_active')
    search_fields = ('email', 'full_name', 'phone')
    ordering = ('-created_at',)
    actions = ['enroll_users', 'revoke_enrollment']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Shaxsiy ma\'lumot', {'fields': ('full_name', 'phone')}),
        ('Kurs kirishi', {'fields': ('is_enrolled', 'enrolled_at', 'enrolled_by')}),
        ('Ruxsatlar', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'full_name', 'phone', 'password1', 'password2'),
        }),
    )

    @admin.action(description="Tanlangan foydalanuvchilarni kursga qo'shish")
    def enroll_users(self, request, queryset):
        queryset.update(is_enrolled=True, enrolled_at=timezone.now(), enrolled_by=request.user)

    @admin.action(description="Tanlangan foydalanuvchilarning kirishini bekor qilish")
    def revoke_enrollment(self, request, queryset):
        queryset.update(is_enrolled=False, enrolled_at=None)
