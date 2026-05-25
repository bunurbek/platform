from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Count, Q
from django.views.decorators.http import require_POST
import json
from datetime import date, timedelta

from apps.accounts.models import CustomUser
from apps.courses.models import Course, Module, Lesson, LessonProgress
from apps.payments.models import PaymentRecord


def dash_only(view_func):
    """Staff-only decorator that redirects to login if not authenticated."""
    return staff_member_required(view_func, login_url='/login/')


# ── HOME ──────────────────────────────────────────────────────────────────────

@dash_only
def dashboard_home(request):
    total_users   = CustomUser.objects.filter(is_staff=False).count()
    enrolled      = CustomUser.objects.filter(is_enrolled=True).count()
    pending       = CustomUser.objects.filter(is_enrolled=False, is_staff=False).count()

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    revenue_month = PaymentRecord.objects.filter(
        created_at__gte=month_start
    ).aggregate(total=Sum('amount'))['total'] or 0
    revenue_total = PaymentRecord.objects.aggregate(total=Sum('amount'))['total'] or 0

    # New signups last 7 days
    week_ago = now - timedelta(days=7)
    new_this_week = CustomUser.objects.filter(created_at__gte=week_ago, is_staff=False).count()

    # Completion rate among enrolled
    course = Course.objects.filter(is_active=True).first()
    avg_completion = 0
    if course and enrolled:
        total_lessons = Lesson.objects.filter(module__course=course).count()
        if total_lessons:
            completed_total = LessonProgress.objects.filter(
                lesson__module__course=course, completed=True
            ).count()
            avg_completion = round((completed_total / (enrolled * total_lessons)) * 100)

    # Recent signups
    recent_users = CustomUser.objects.filter(is_staff=False).order_by('-created_at')[:5]

    return render(request, 'dashboard/home.html', {
        'total_users': total_users,
        'enrolled': enrolled,
        'pending': pending,
        'revenue_month': revenue_month,
        'revenue_total': revenue_total,
        'new_this_week': new_this_week,
        'avg_completion': avg_completion,
        'recent_users': recent_users,
    })


# ── STUDENTS ──────────────────────────────────────────────────────────────────

@dash_only
def students(request):
    q      = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')  # 'enrolled' | 'pending' | ''

    qs = CustomUser.objects.filter(is_staff=False).order_by('-created_at')
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(full_name__icontains=q) | Q(phone__icontains=q))
    if status == 'enrolled':
        qs = qs.filter(is_enrolled=True)
    elif status == 'pending':
        qs = qs.filter(is_enrolled=False)

    # Attach progress counts
    course = Course.objects.filter(is_active=True).first()
    total_lessons = Lesson.objects.filter(module__course=course).count() if course else 0
    progress_counts = {}
    if course:
        for p in LessonProgress.objects.filter(completed=True, lesson__module__course=course).values('user_id').annotate(n=Count('id')):
            progress_counts[str(p['user_id'])] = p['n']

    users_data = []
    for u in qs:
        users_data.append({
            'user': u,
            'completed': progress_counts.get(str(u.pk), 0),
            'total': total_lessons,
        })

    return render(request, 'dashboard/students.html', {
        'users_data': users_data,
        'q': q,
        'status': status,
        'total_count': qs.count(),
    })


@dash_only
def student_detail(request, pk):
    student = get_object_or_404(CustomUser, pk=pk, is_staff=False)
    course  = Course.objects.filter(is_active=True).first()
    lessons = Lesson.objects.filter(module__course=course).order_by('module__order', 'order') if course else []
    progress_map = {}
    if course:
        for p in LessonProgress.objects.filter(user=student, lesson__module__course=course):
            progress_map[p.lesson_id] = p
    payments = PaymentRecord.objects.filter(user=student).order_by('-created_at')

    return render(request, 'dashboard/student_detail.html', {
        'student': student,
        'lessons': lessons,
        'progress_map': progress_map,
        'payments': payments,
        'course': course,
    })


@dash_only
@require_POST
def enroll_student(request, pk):
    student = get_object_or_404(CustomUser, pk=pk, is_staff=False)
    data = json.loads(request.body)
    amount    = data.get('amount', 300)
    reference = data.get('reference', '')
    pay_date  = data.get('date', str(date.today()))
    notes     = data.get('notes', '')

    PaymentRecord.objects.create(
        user=student,
        amount=amount,
        payment_reference=reference,
        payment_date=pay_date,
        recorded_by=request.user,
        notes=notes,
    )
    student.is_enrolled = True
    student.enrolled_at = timezone.now()
    student.enrolled_by = request.user
    student.save()

    return JsonResponse({'ok': True, 'name': student.display_name})


@dash_only
@require_POST
def revoke_student(request, pk):
    student = get_object_or_404(CustomUser, pk=pk, is_staff=False)
    student.is_enrolled = False
    student.enrolled_at = None
    student.save()
    return JsonResponse({'ok': True})


# ── PAYMENTS ──────────────────────────────────────────────────────────────────

@dash_only
def payments(request):
    records = PaymentRecord.objects.select_related('user', 'recorded_by').order_by('-created_at')
    total   = records.aggregate(t=Sum('amount'))['t'] or 0
    return render(request, 'dashboard/payments.html', {
        'records': records,
        'total': total,
    })


# ── CONTENT ───────────────────────────────────────────────────────────────────

@dash_only
def content(request):
    course  = Course.objects.filter(is_active=True).first()
    modules = course.modules.prefetch_related('lessons').all() if course else []
    return render(request, 'dashboard/content.html', {
        'course': course,
        'modules': modules,
    })


# ── Module CRUD ──────────────────────────────────────────────────────────────

@dash_only
@require_POST
def module_add(request):
    course = get_object_or_404(Course, is_active=True)
    data   = json.loads(request.body)
    next_order = (course.modules.aggregate(m=Count('id'))['m'] or 0) + 1
    m = Module.objects.create(
        course=course,
        title=data.get('title', 'Yangi modul'),
        description=data.get('description', ''),
        order=next_order,
        is_free=data.get('is_free', False),
    )
    return JsonResponse({'ok': True, 'id': m.pk, 'title': m.title, 'order': m.order})


@dash_only
@require_POST
def module_toggle_free(request, pk):
    m = get_object_or_404(Module, pk=pk)
    m.is_free = not m.is_free
    m.save()
    return JsonResponse({'ok': True, 'is_free': m.is_free})


@dash_only
@require_POST
def module_delete(request, pk):
    m = get_object_or_404(Module, pk=pk)
    m.delete()
    return JsonResponse({'ok': True})


# ── Lesson CRUD (within a module) ───────────────────────────────────────────

@dash_only
@require_POST
def lesson_add(request, module_pk):
    module = get_object_or_404(Module, pk=module_pk)
    data   = json.loads(request.body)
    next_order = (module.lessons.aggregate(m=Count('id'))['m'] or 0) + 1
    lesson = Lesson.objects.create(
        module=module,
        title=data.get('title', 'Yangi dars'),
        description=data.get('description', ''),
        video_url=data.get('video_url', ''),
        order=next_order,
        duration_minutes=int(data.get('duration_minutes') or 0),
    )
    return JsonResponse({
        'ok': True, 'id': lesson.pk,
        'title': lesson.title, 'order': lesson.order,
        'duration_minutes': lesson.duration_minutes,
    })


@dash_only
@require_POST
def lesson_delete(request, pk):
    lesson = get_object_or_404(Lesson, pk=pk)
    lesson.delete()
    return JsonResponse({'ok': True})


@dash_only
@require_POST
def lesson_update(request, pk):
    """Multipart update — title, description, video URL, duration, homework PDF, homework note.
    Accepts multipart/form-data so a homework PDF file can be uploaded in the same request."""
    lesson = get_object_or_404(Lesson, pk=pk)

    if 'title' in request.POST:           lesson.title = request.POST['title'].strip() or lesson.title
    if 'description' in request.POST:     lesson.description = request.POST.get('description', '')
    if 'video_url' in request.POST:       lesson.video_url = request.POST.get('video_url', '')
    if 'duration_minutes' in request.POST:
        try: lesson.duration_minutes = int(request.POST.get('duration_minutes') or 0)
        except ValueError: pass
    if 'homework_note' in request.POST:   lesson.homework_note = request.POST.get('homework_note', '')

    if 'homework_pdf' in request.FILES:
        lesson.homework_pdf = request.FILES['homework_pdf']
    elif request.POST.get('remove_homework') == '1' and lesson.homework_pdf:
        lesson.homework_pdf.delete(save=False)
        lesson.homework_pdf = None

    lesson.save()
    return JsonResponse({
        'ok': True,
        'has_homework': bool(lesson.homework_pdf),
        'homework_url': lesson.homework_pdf.url if lesson.homework_pdf else '',
    })


# ── ANALYTICS ─────────────────────────────────────────────────────────────────

@dash_only
def analytics(request):
    # Enrollments by month (last 6 months)
    months_data = []
    now = timezone.now()
    for i in range(5, -1, -1):
        d = (now - timedelta(days=30 * i))
        label = d.strftime('%b %Y')
        count = CustomUser.objects.filter(
            is_enrolled=True,
            enrolled_at__year=d.year,
            enrolled_at__month=d.month,
        ).count()
        rev = PaymentRecord.objects.filter(
            payment_date__year=d.year,
            payment_date__month=d.month,
        ).aggregate(t=Sum('amount'))['t'] or 0
        months_data.append({'label': label, 'enrollments': count, 'revenue': float(rev)})

    course = Course.objects.filter(is_active=True).first()
    lesson_stats = []
    if course:
        total_enrolled = CustomUser.objects.filter(is_enrolled=True).count() or 1
        for module in course.modules.prefetch_related('lessons').all():
            for lesson in module.lessons.all():
                done = LessonProgress.objects.filter(lesson=lesson, completed=True).count()
                lesson_stats.append({
                    'title': f"M{module.order}.{lesson.order} {lesson.title}",
                    'pct': round((done / total_enrolled) * 100),
                    'done': done,
                })

    return render(request, 'dashboard/analytics.html', {
        'months_data': json.dumps(months_data),
        'lesson_stats': lesson_stats,
        'total_revenue': PaymentRecord.objects.aggregate(t=Sum('amount'))['t'] or 0,
        'total_enrolled': CustomUser.objects.filter(is_enrolled=True).count(),
    })
