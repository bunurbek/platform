from django.shortcuts import render, redirect
from apps.courses.models import Course


def landing(request):
    # Logged-in students go straight to the app; admins keep landing access
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect('course_home')

    course = Course.objects.filter(is_active=True).first()
    modules = course.modules.prefetch_related('lessons').all() if course else []
    return render(request, 'landing/index.html', {
        'course': course,
        'modules': modules,
        'total_modules': len(modules),
    })
