import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email majburiy')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, blank=True, null=True)
    full_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    # Telegram fields (primary auth)
    telegram_id       = models.BigIntegerField(unique=True, null=True, blank=True)
    telegram_username = models.CharField(max_length=64, blank=True)
    telegram_photo    = models.URLField(blank=True)
    is_enrolled = models.BooleanField(default=False)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    enrolled_by = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='enrolled_students'
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'Foydalanuvchi'
        verbose_name_plural = 'Foydalanuvchilar'

    def __str__(self):
        return self.full_name or self.email

    @property
    def display_name(self):
        if self.full_name:
            return self.full_name
        if self.telegram_username:
            return f"@{self.telegram_username}"
        if self.email:
            return self.email.split('@')[0]
        return f"user-{str(self.id)[:6]}"


class TelegramAuthSession(models.Model):
    """Conversational auth: web creates token → user opens bot → bot collects contact+name → user clicks auth link → web logs in."""
    STATUS = [
        ('pending',           'Telegram kutilmoqda'),
        ('awaiting_contact',  'Kontakt kutilmoqda'),
        ('awaiting_name',     'Ism kutilmoqda'),
        ('ready',             'Auth tayyor'),
        ('verified',          'Tasdiqlandi'),
        ('expired',           'Muddati o\'tdi'),
    ]
    token       = models.CharField(max_length=64, unique=True, db_index=True)  # passed in /start
    code        = models.CharField(max_length=6, blank=True)  # 6-digit code shown in bot
    status      = models.CharField(max_length=20, choices=STATUS, default='pending')
    telegram_id         = models.BigIntegerField(null=True, blank=True)
    telegram_username   = models.CharField(max_length=64, blank=True)
    telegram_first_name = models.CharField(max_length=128, blank=True)
    telegram_photo      = models.URLField(blank=True)
    phone               = models.CharField(max_length=32, blank=True)
    collected_name      = models.CharField(max_length=200, blank=True)
    user        = models.ForeignKey('CustomUser', null=True, blank=True, on_delete=models.SET_NULL)
    created_at  = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def is_expired(self):
        return (timezone.now() - self.created_at).total_seconds() > 600  # 10 min
