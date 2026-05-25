from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Course, Module, Lesson, LessonProgress


@login_required
def course_home(request):
    """Course overview — all modules with progress rings + dropdowns."""
    course = get_object_or_404(Course, is_active=True)
    modules = course.modules.prefetch_related('lessons').all()

    # Build per-module display data
    modules_data = []
    overall_completed = 0
    overall_total = 0
    modules_list = list(modules)
    for idx, m in enumerate(modules_list):
        total = m.lesson_count
        done = m.lessons_completed(request.user)
        overall_total += total
        overall_completed += done
        # If module is cooldown-locked, find the cooldown source (previous module)
        lock_reason = m.lock_reason_for(request.user)
        cooldown_secs = None
        if lock_reason == 'cooldown':
            prev = modules_list[idx - 1] if idx > 0 else None
            if prev:
                cooldown_secs = prev.cooldown_seconds_remaining(request.user)
        modules_data.append({
            'module': m,
            'total': total,
            'done': done,
            'percent': int((done / total * 100)) if total else 0,
            'is_complete': m.is_complete(request.user),
            'is_accessible': m.is_accessible(request.user),
            'lock_reason': lock_reason,
            'cooldown_seconds': cooldown_secs,
            'lessons': list(m.lessons.all()),
            'completed_lesson_ids': set(
                LessonProgress.objects.filter(
                    user=request.user, lesson__module=m, completed=True
                ).values_list('lesson_id', flat=True)
            ),
        })

    overall_pct = int((overall_completed / overall_total * 100)) if overall_total else 0

    # Find the next lesson to continue with (first incomplete lesson in an accessible module)
    continue_lesson = None
    continue_module = None
    continue_is_first = True
    waiting_for_cooldown = None  # next module that's currently cooldown-locked

    for md in modules_data:
        if md['is_accessible']:
            for l in md['lessons']:
                if l.pk not in md['completed_lesson_ids']:
                    continue_lesson = l
                    continue_module = md['module']
                    continue_is_first = (overall_completed == 0)
                    break
            if continue_lesson:
                break
        elif md['lock_reason'] == 'cooldown' and not waiting_for_cooldown:
            # First cooldown-locked module — we'll show its countdown
            waiting_for_cooldown = md

    return render(request, 'courses/course_home.html', {
        'course': course,
        'modules_data': modules_data,
        'overall_completed': overall_completed,
        'overall_total': overall_total,
        'overall_pct': overall_pct,
        'continue_lesson': continue_lesson,
        'continue_module': continue_module,
        'continue_is_first': continue_is_first,
        'waiting_for_cooldown': waiting_for_cooldown,
    })


@login_required
def free_lesson(request):
    """Redirects to the first incomplete (or first) lesson of the free module.
    The unified lesson view handles all the UI."""
    course = get_object_or_404(Course, is_active=True)
    free_module = course.modules.filter(is_free=True).order_by('order').first() \
                  or course.modules.order_by('order').first()
    if not free_module:
        return redirect('landing')

    completed_ids = set(LessonProgress.objects.filter(
        user=request.user, lesson__module=free_module, completed=True
    ).values_list('lesson_id', flat=True))
    lessons = list(free_module.lessons.order_by('order'))
    target = next((l for l in lessons if l.pk not in completed_ids), None) or (lessons[0] if lessons else None)
    if not target:
        return redirect('course_home')
    return redirect('lesson', pk=target.pk)


@login_required
def lesson_view(request, pk):
    """Play a specific sub-lesson within a module."""
    lesson = get_object_or_404(Lesson, pk=pk)
    module = lesson.module
    course = module.course

    if not lesson.is_accessible(request.user):
        prev_module = course.modules.filter(order__lt=module.order).order_by('-order').first()
        lock_reason = module.lock_reason_for(request.user)
        cooldown_secs = prev_module.cooldown_seconds_remaining(request.user) if (lock_reason == 'cooldown' and prev_module) else None
        return render(request, 'courses/lesson_locked.html', {
            'lesson': lesson,
            'module': module,
            'course': course,
            'prev_module': prev_module,
            'lock_reason': lock_reason,
            'cooldown_seconds': cooldown_secs,
            'prev_done': prev_module.lessons_completed(request.user) if prev_module else 0,
            'prev_total': prev_module.lesson_count if prev_module else 0,
        })

    progress, _ = LessonProgress.objects.get_or_create(user=request.user, lesson=lesson)

    # Sub-lessons in this module — for the numbered strip
    module_lessons = list(module.lessons.order_by('order'))
    idx = next((i for i, l in enumerate(module_lessons) if l.pk == lesson.pk), 0)
    prev_lesson = module_lessons[idx - 1] if idx > 0 else None
    next_lesson = module_lessons[idx + 1] if idx + 1 < len(module_lessons) else None
    completed_in_module = set(LessonProgress.objects.filter(
        user=request.user, lesson__module=module, completed=True
    ).values_list('lesson_id', flat=True))

    # If at the end of this module, see if next module is accessible
    next_module = None
    if not next_lesson:
        next_module = course.modules.filter(order__gt=module.order).order_by('order').first()

    # Sidebar: full course outline with lock state (for desktop sidebar + mobile drawer)
    modules_outline = []
    for m in course.modules.prefetch_related('lessons').all():
        modules_outline.append({
            'module': m,
            'lessons': list(m.lessons.all()),
            'is_accessible': m.is_accessible(request.user),
            'completed_lesson_ids': set(
                LessonProgress.objects.filter(
                    user=request.user, lesson__module=m, completed=True
                ).values_list('lesson_id', flat=True)
            ),
            'is_current_module': m.pk == module.pk,
        })

    # Show conversion modal trigger: unenrolled user finishing the last lesson of the free module
    is_last_in_free_module = (
        module.is_free
        and not request.user.is_enrolled
        and lesson.pk == module_lessons[-1].pk if module_lessons else False
    )

    return render(request, 'courses/lesson.html', {
        'lesson': lesson,
        'module': module,
        'course': course,
        'progress': progress,
        'prev_lesson': prev_lesson,
        'next_lesson': next_lesson,
        'next_module': next_module,
        'modules_outline': modules_outline,
        'module_lessons': module_lessons,
        'completed_in_module': completed_in_module,
        'lesson_index': idx,
        'show_conversion_on_end': is_last_in_free_module,
    })


@login_required
@require_POST
def mark_complete(request, pk):
    lesson = get_object_or_404(Lesson, pk=pk)
    if not lesson.is_accessible(request.user):
        return JsonResponse({'error': 'unauthorized'}, status=403)

    progress, _ = LessonProgress.objects.get_or_create(user=request.user, lesson=lesson)
    progress.completed = True
    progress.completed_at = timezone.now()
    progress.save()

    module = lesson.module
    module_done = module.lessons_completed(request.user)
    module_total = module.lesson_count
    module_complete = module_done >= module_total

    # If module just completed, next one is gated by cooldown
    next_module = module.course.modules.filter(order__gt=module.order).order_by('order').first()
    cooldown_secs = None
    next_unlocked = False
    if module_complete and next_module:
        next_unlocked = next_module.is_accessible(request.user)
        if not next_unlocked:
            cooldown_secs = module.cooldown_seconds_remaining(request.user)

    return JsonResponse({
        'completed': True,
        'module_done': module_done,
        'module_total': module_total,
        'module_complete': module_complete,
        'next_module_unlocked': next_unlocked,
        'next_module_id': next_module.pk if next_module else None,
        'cooldown_seconds': cooldown_secs,
    })
