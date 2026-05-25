import secrets
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import CustomUser, TelegramAuthSession


# ── TELEGRAM AUTH ──────────────────────────────────────────────────────────────

def tg_start(request):
    """User clicks 'Bepul boshlash' — generate a session token, show the Telegram redirect page.
    Re-uses existing pending session if user is already mid-flow."""
    token = request.session.get('tg_auth_token')
    reuse = False
    if token:
        try:
            existing = TelegramAuthSession.objects.get(token=token)
            if not existing.is_expired() and existing.status != 'verified':
                reuse = True
        except TelegramAuthSession.DoesNotExist:
            pass
    if not reuse:
        token = secrets.token_urlsafe(16)
        TelegramAuthSession.objects.create(token=token)
        request.session['tg_auth_token'] = token

    bot_username = settings.TELEGRAM_BOT_USERNAME or 'bunurbekauth_bot'
    return render(request, 'accounts/tg_start.html', {
        'bot_username': bot_username,
        'tg_link': f"https://t.me/{bot_username}?start={token}",
        'token': token,
    })


def tg_waiting(request):
    """Waiting room — page polls until the bot collected everything and user clicked auth link."""
    token = request.session.get('tg_auth_token')
    if not token:
        return redirect('tg_start_view')
    bot_username = settings.TELEGRAM_BOT_USERNAME or 'bunurbekauth_bot'
    return render(request, 'accounts/tg_waiting.html', {
        'token': token,
        'bot_username': bot_username,
        'tg_link': f"https://t.me/{bot_username}?start={token}",
    })


@require_POST
def tg_status(request):
    """AJAX: poll to check if session has progressed. Auto-login when status='verified'."""
    token = request.session.get('tg_auth_token')
    if not token:
        return JsonResponse({'state': 'no_session'})
    try:
        s = TelegramAuthSession.objects.get(token=token)
    except TelegramAuthSession.DoesNotExist:
        return JsonResponse({'state': 'no_session'})
    if s.is_expired():
        return JsonResponse({'state': 'expired'})

    # If verified (user already finalized via auth link), log them in here too
    if s.status == 'verified' and s.user and not request.user.is_authenticated:
        login(request, s.user, backend='django.contrib.auth.backends.ModelBackend')
        request.session.pop('tg_auth_token', None)
        return JsonResponse({'state': 'verified', 'redirect': '/kurs/bepul-dars/'})

    return JsonResponse({
        'state': s.status,
        'first_name': s.telegram_first_name,
        'name': s.collected_name,
        'phone': s.phone,
    })


def tg_finish(request, token):
    """Finalize auth — called when user clicks the magic link inside Telegram.

    Creates/finds the user with all collected data, marks session verified,
    logs them in, and redirects to the free lesson."""
    try:
        s = TelegramAuthSession.objects.get(token=token)
    except TelegramAuthSession.DoesNotExist:
        return render(request, 'accounts/tg_error.html', {'error': "Sessiya topilmadi"})

    if s.is_expired():
        return render(request, 'accounts/tg_error.html', {'error': "Sessiya muddati o'tdi"})

    if s.status not in ('ready', 'verified'):
        return render(request, 'accounts/tg_error.html', {
            'error': "Avval botda barcha ma'lumotlarni to'ldiring"
        })

    # Find or create user by telegram_id
    user, created = CustomUser.objects.get_or_create(
        telegram_id=s.telegram_id,
        defaults={
            'full_name': s.collected_name or s.telegram_first_name,
            'telegram_username': s.telegram_username,
            'telegram_photo': s.telegram_photo,
            'phone': s.phone,
        }
    )
    if not created:
        user.full_name = s.collected_name or user.full_name or s.telegram_first_name
        user.telegram_username = s.telegram_username or user.telegram_username
        user.telegram_photo = s.telegram_photo or user.telegram_photo
        user.phone = s.phone or user.phone
        user.save()

    s.status = 'verified'
    s.user = user
    s.verified_at = timezone.now()
    s.save()

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    request.session.pop('tg_auth_token', None)
    return redirect('free_lesson')


# ── BACKUP EMAIL LOGIN (kept for admin / fallback) ─────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('course_home')
    if request.method == 'POST':
        from django.contrib.auth import authenticate
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            if user.is_staff:
                return redirect('dashboard_home')
            return redirect(request.GET.get('next') or 'course_home')
        messages.error(request, "Email yoki parol noto'g'ri")
    return render(request, 'accounts/login.html')


def logout_view(request):
    logout(request)
    return redirect('landing')


@login_required
def profile_view(request):
    return render(request, 'accounts/profile.html')
