"""Restructure: Course → Module → Lesson hierarchy.
Drops old Lesson schema and rebuilds it FK'd to a new Module model.
Safe because no real student data exists yet (dev DB)."""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('courses', '0001_initial'),
    ]

    operations = [
        # 1. Drop dependent + lesson tables (they're empty)
        migrations.DeleteModel(name='LessonProgress'),
        migrations.DeleteModel(name='Lesson'),

        # 2. Create Module
        migrations.CreateModel(
            name='Module',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
                ('order', models.PositiveIntegerField(default=0)),
                ('is_free', models.BooleanField(default=False, help_text="Bepul namuna modul (ro'yxatdan o'tgan har kim ko'ra oladi)")),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='modules', to='courses.course')),
            ],
            options={'verbose_name': 'Modul', 'verbose_name_plural': 'Modullar', 'ordering': ['order']},
        ),

        # 3. Re-create Lesson with module FK (new schema)
        migrations.CreateModel(
            name='Lesson',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
                ('video_file', models.FileField(blank=True, upload_to='videos/')),
                ('video_url', models.URLField(blank=True, help_text='YouTube embed yoki direct video URL')),
                ('order', models.PositiveIntegerField(default=0)),
                ('duration_minutes', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('module', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lessons', to='courses.module')),
            ],
            options={'verbose_name': 'Dars (video)', 'verbose_name_plural': 'Darslar (videolar)', 'ordering': ['order']},
        ),

        # 4. Re-create LessonProgress
        migrations.CreateModel(
            name='LessonProgress',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('completed', models.BooleanField(default=False)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('watch_time_seconds', models.PositiveIntegerField(default=0)),
                ('lesson', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='progress', to='courses.lesson')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='progress', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'Dars jarayoni', 'verbose_name_plural': 'Dars jarayonlari', 'unique_together': {('user', 'lesson')}},
        ),
    ]
