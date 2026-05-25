from django.db import models
from django.db.models import Max
from django.conf import settings
from django.utils import timezone


class Course(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    thumbnail = models.ImageField(upload_to='courses/', blank=True)
    price = models.DecimalField(max_digits=6, decimal_places=2, default=300.00)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Kurs'
        verbose_name_plural = 'Kurslar'

    def __str__(self):
        return self.title


class Module(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='modules')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    thumbnail = models.ImageField(upload_to='modules/', blank=True, null=True,
                                  help_text='Modul uchun rasm (kvadrat, JPG/PNG)')
    order = models.PositiveIntegerField(default=0)
    is_free = models.BooleanField(default=False, help_text='Bepul namuna modul (ro\'yxatdan o\'tgan har kim ko\'ra oladi)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Modul'
        verbose_name_plural = 'Modullar'
        ordering = ['order']

    def __str__(self):
        return f"{self.order}. {self.title}"

    @property
    def total_duration(self):
        return sum(l.duration_minutes for l in self.lessons.all())

    @property
    def lesson_count(self):
        return self.lessons.count()

    def lessons_completed(self, user):
        if not user.is_authenticated:
            return 0
        return LessonProgress.objects.filter(
            user=user, lesson__module=self, completed=True
        ).count()

    def progress_percent(self, user):
        total = self.lesson_count
        if total == 0:
            return 0
        return int((self.lessons_completed(user) / total) * 100)

    def is_complete(self, user):
        total = self.lesson_count
        if total == 0:
            return False
        return self.lessons_completed(user) >= total

    def completed_at_for(self, user):
        """Timestamp when the user finished the LAST lesson in this module. None if not complete."""
        if not user.is_authenticated or not self.is_complete(user):
            return None
        return LessonProgress.objects.filter(
            user=user, lesson__module=self, completed=True,
        ).aggregate(latest=Max('completed_at'))['latest']

    def cooldown_seconds_remaining(self, user):
        """How many seconds until cooldown ends (after completing this module).
        Returns None if module not complete. 0 if cooldown elapsed."""
        cooldown = getattr(settings, 'MODULE_COOLDOWN_SECONDS', 86400)
        if cooldown <= 0:
            return 0
        completed_at = self.completed_at_for(user)
        if not completed_at:
            return None
        elapsed = (timezone.now() - completed_at).total_seconds()
        remaining = cooldown - elapsed
        return max(0, int(remaining))

    def is_accessible(self, user):
        """Module is accessible if:
        - It's the free module (always), OR
        - User is enrolled AND all previous modules are complete
          AND 24h cooldown has elapsed since previous module's completion.
        """
        if self.is_free:
            return True
        if not user.is_authenticated or not user.is_enrolled:
            return False
        prev = self.course.modules.filter(order__lt=self.order).order_by('-order').first()
        if not prev:
            return True
        if not prev.is_complete(user):
            return False
        # Cooldown check
        remaining = prev.cooldown_seconds_remaining(user)
        if remaining and remaining > 0:
            return False
        return True

    def lock_reason_for(self, user):
        """One of: 'open', 'not_enrolled', 'progression', 'cooldown'."""
        if self.is_accessible(user):
            return 'open'
        if not user.is_authenticated or not user.is_enrolled:
            return 'not_enrolled'
        prev = self.course.modules.filter(order__lt=self.order).order_by('-order').first()
        if prev and prev.is_complete(user):
            remaining = prev.cooldown_seconds_remaining(user)
            if remaining and remaining > 0:
                return 'cooldown'
        return 'progression'


class Lesson(models.Model):
    """A single video lesson within a Module (5-15 min typical)."""
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name='lessons')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, help_text='Dars haqida ma\'lumot — talaba nimani o\'rganadi')
    video_file = models.FileField(upload_to='videos/', blank=True)
    video_url = models.URLField(blank=True, help_text='YouTube embed yoki direct video URL')
    order = models.PositiveIntegerField(default=0)
    duration_minutes = models.PositiveIntegerField(default=0)
    # Homework — optional PDF + note. When PDF exists, students see Download + Telegram send buttons.
    homework_pdf = models.FileField(upload_to='homework/', blank=True, null=True,
                                    help_text='Vazifa PDF fayli (ixtiyoriy)')
    homework_note = models.TextField(blank=True, help_text='Vazifa haqida qisqacha izoh')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dars (video)"
        verbose_name_plural = "Darslar (videolar)"
        ordering = ['order']

    def __str__(self):
        return f"{self.module.order}.{self.order} {self.title}"

    def is_accessible(self, user):
        """Lesson access is gated by module access."""
        return self.module.is_accessible(user)

    def is_completed_by(self, user):
        if not user.is_authenticated:
            return False
        return LessonProgress.objects.filter(user=user, lesson=self, completed=True).exists()


class LessonProgress(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='progress')
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='progress')
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    watch_time_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('user', 'lesson')
        verbose_name = "Dars jarayoni"
        verbose_name_plural = "Dars jarayonlari"

    def __str__(self):
        return f"{self.user} — {self.lesson}"
