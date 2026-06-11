"""
Telegram authentication bot — conversational flow.

Run with:
    python manage.py run_telegram_bot

Flow inside Telegram:
1. /start <token>           → Bot asks for contact (Share Contact button)
2. User shares contact      → Bot saves phone, asks for name
3. User types name          → Bot saves name, sends "Authorize" button
4. User clicks button       → Opens https://site.com/start/finish/<token>/ → logged in
"""
import logging
import time
import os
from io import BytesIO
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import close_old_connections
from django.db.models import Sum, Count
from django.utils import timezone
import requests

from apps.accounts.models import TelegramAuthSession, CustomUser

logging.basicConfig(
    format='%(asctime)s — %(levelname)s — %(message)s',
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# Public URL where the website is reachable for the auth link.
SITE_URL = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')

# ── Admin Telegram user IDs (whoever is allowed to use /admin) ────────────────
# Hardcoded defaults + optional override via ADMIN_TELEGRAM_IDS env var
_DEFAULT_ADMINS = {5451704373, 2054394911, 7099268533}
_env = os.environ.get('ADMIN_TELEGRAM_IDS', '')
ADMIN_IDS = _DEFAULT_ADMINS | {int(x.strip()) for x in _env.split(',') if x.strip().isdigit()}

# Per-chat state for multi-step admin flows (search, broadcast, enroll, etc.)
# Maps chat_id -> (mode_string, payload_dict)
_admin_state = {}


def _is_admin(tg_id):
    return tg_id in ADMIN_IDS


def get_session_by_token(token):
    try:
        s = TelegramAuthSession.objects.get(token=token)
        if s.is_expired():
            s.status = 'expired'
            s.save()
            return None
        return s
    except TelegramAuthSession.DoesNotExist:
        return None


def get_session_by_telegram_id(tg_id):
    """Latest non-finalized session for this Telegram user."""
    qs = TelegramAuthSession.objects.filter(
        telegram_id=tg_id,
    ).exclude(status__in=['verified', 'expired']).order_by('-created_at')
    for s in qs:
        if not s.is_expired():
            return s
        s.status = 'expired'
        s.save()
    return None


class Command(BaseCommand):
    help = 'Run the Telegram authentication bot (raw long-polling)'

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            self.stderr.write(self.style.ERROR('TELEGRAM_BOT_TOKEN env var is not set'))
            return

        self.base = f"https://api.telegram.org/bot{token}"
        offset = 0

        self.stdout.write(self.style.SUCCESS(
            f"✓ Bot started: @{settings.TELEGRAM_BOT_USERNAME}\n"
            f"  SITE_URL = {SITE_URL}\n"
            f"  Listening for messages...\n"
        ))

        while True:
            try:
                resp = requests.get(
                    f"{self.base}/getUpdates",
                    params={'offset': offset, 'timeout': 10,
                            'allowed_updates': '["message","callback_query"]'},
                    timeout=15,
                )
                data = resp.json()
                if not data.get('ok'):
                    log.error(f"Telegram API error: {data}")
                    time.sleep(3)
                    continue

                for update in data.get('result', []):
                    offset = update['update_id'] + 1
                    # Drop stale Postgres connections before each handler — long-running
                    # daemons need this since Django does not auto-reconnect.
                    close_old_connections()
                    try:
                        self._handle_update(update)
                    except Exception as e:
                        log.exception(f"Update error: {e}")
                        # If DB went down mid-handler, force reconnect on next loop
                        close_old_connections()

            except requests.exceptions.Timeout:
                # Long-poll cycle elapsed with no updates — also a good time to refresh
                close_old_connections()
                continue
            except KeyboardInterrupt:
                self.stdout.write("\nBot stopped.")
                break
            except Exception as e:
                log.error(f"Polling error: {e}")
                close_old_connections()
                time.sleep(3)

    # ── Update routing ────────────────────────────────────────────────────────

    def _handle_update(self, update):
        # Inline button presses
        if 'callback_query' in update:
            self._handle_callback(update['callback_query'])
            return

        msg = update.get('message')
        if not msg:
            return

        chat_id = msg['chat']['id']
        tg_user = msg.get('from', {})
        tg_id   = tg_user.get('id')
        text    = (msg.get('text') or '').strip()
        contact = msg.get('contact')

        # ── Admin commands (only for whitelisted Telegram IDs) ────────────────
        if _is_admin(tg_id):
            # Multi-step admin flows: search / broadcast / enroll
            mode, payload = _admin_state.get(chat_id, (None, {}))
            if mode and text and not text.startswith('/'):
                self._handle_admin_input(chat_id, tg_id, text, mode, payload)
                return

            if text == '/admin' or text.startswith('/admin '):
                _admin_state.pop(chat_id, None)
                self._show_admin_menu(chat_id)
                return
            if text == '/stats':
                self._show_admin_dashboard(chat_id)
                return
            if text == '/cancel':
                _admin_state.pop(chat_id, None)
                self._send(chat_id, "❌ Bekor qilindi.")
                return

        # 1. /start TOKEN → ask for contact
        if text.startswith('/start'):
            parts = text.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                self._send(chat_id,
                    "👋 Salom! Bu *Bu Nurbek* kursi avtorizatsiya boti.\n\n"
                    "Saytdan \"Bepul boshlash\" tugmasini bosib bu yerga qayting.")
                return

            session_token = parts[1].strip()
            session = get_session_by_token(session_token)
            if not session:
                self._send(chat_id,
                    "❌ Sessiya topilmadi yoki muddati o'tdi.\n"
                    "Saytdan qaytadan \"Bepul boshlash\" tugmasini bosing.")
                return

            session.telegram_id         = tg_id
            session.telegram_username   = tg_user.get('username', '') or ''
            session.telegram_first_name = tg_user.get('first_name', '') or ''
            session.save()
            log.info(f"Session {session_token[:8]}... linked to @{tg_user.get('username') or tg_id}")

            # If this Telegram user already has an account, skip contact + name
            existing = CustomUser.objects.filter(telegram_id=tg_id).first()
            if existing:
                import random
                code = f"{random.randint(0, 999999):06d}"
                session.collected_name = existing.full_name or session.telegram_first_name
                session.phone = existing.phone or ''
                session.code = code
                session.status = 'ready'
                session.save()
                log.info(f"Returning user @{tg_user.get('username') or tg_id} — code {code}")
                pretty = " ".join(code[:3]) + " — " + " ".join(code[3:])
                self._send(chat_id,
                    f"👋 Xush kelibsiz, *{existing.display_name}*!\n\n"
                    f"Sizning kirish kodingiz:\n\n`{pretty}`\n\n"
                    f"Saytga qayting va kodni kiriting.\n"
                    f"_Kod 10 daqiqa amal qiladi._")
                return

            # New user — collect contact first
            session.status = 'awaiting_contact'
            session.save()
            greet_name = session.telegram_first_name or "do'st"
            self._send_with_contact_button(chat_id,
                f"👋 Salom, *{greet_name}*!\n\n"
                f"Birinchi darsni bepul ochish uchun *telefon raqamingizni* ulashing.\n\n"
                f"Pastdagi tugmani bosing 👇")
            return

        # 2. Contact shared → ask for name
        if contact:
            session = get_session_by_telegram_id(tg_id)
            if not session:
                self._send(chat_id,
                    "❌ Sessiya topilmadi. Saytdan \"Bepul boshlash\" tugmasini bosing.")
                return

            phone = contact.get('phone_number', '')
            if not phone.startswith('+'):
                phone = '+' + phone
            session.phone = phone
            session.status = 'awaiting_name'
            session.save()
            log.info(f"Contact received from {tg_id}: {phone}")

            self._send_remove_keyboard(chat_id,
                f"✅ Telefon qabul qilindi: `{phone}`\n\n"
                f"Endi *ism va familiyangizni* yozing:\n"
                f"_Masalan: Jasur Toshmatov_")
            return

        # 3. Name text → save name and send auth link
        if text:
            session = get_session_by_telegram_id(tg_id)
            if not session:
                self._send(chat_id,
                    "Saytdan \"Bepul boshlash\" tugmasini bosib qaytadan boshlang.")
                return

            if session.status != 'awaiting_name':
                # User chatting outside the flow — give a hint based on current stage
                if session.status == 'awaiting_contact':
                    self._send_with_contact_button(chat_id,
                        "Avval telefon raqamingizni ulashing 👇")
                elif session.status == 'ready' and session.code:
                    # Already got code — show it again
                    pretty = " ".join(session.code[:3]) + " — " + " ".join(session.code[3:])
                    self._send(chat_id,
                        f"Sizning kodingiz hali ham amal qiladi:\n\n`{pretty}`\n\n"
                        "Saytga qayting va kiriting.")
                else:
                    self._send(chat_id,
                        "Saytdan \"Bepul boshlash\" tugmasini bosib qaytadan boshlang.")
                return

            name = text[:200].strip()
            if len(name) < 2:
                self._send(chat_id, "Iltimos, to'liq ism kiriting.")
                return

            # Generate a fresh 6-digit code
            import random
            code = f"{random.randint(0, 999999):06d}"

            session.collected_name = name
            session.code = code
            session.status = 'ready'
            session.save()

            log.info(f"Code {code} issued to @{tg_user.get('username') or tg_user.get('id')} for session {session.token[:8]}...")

            pretty = " ".join(code[:3]) + " — " + " ".join(code[3:])
            self._send(chat_id,
                f"✅ Rahmat, *{name}*!\n\n"
                f"Sizning kirish kodingiz:\n\n"
                f"`{pretty}`\n\n"
                f"📋 Bu kodni nusxalab, saytga qayting va kiriting.\n"
                f"_Kod 10 daqiqa amal qiladi._")

    # ── Telegram API helpers ──────────────────────────────────────────────────

    def _send(self, chat_id, text):
        try:
            requests.post(f"{self.base}/sendMessage", json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'Markdown',
            }, timeout=10)
        except Exception as e:
            log.error(f"sendMessage failed: {e}")

    def _send_with_contact_button(self, chat_id, text):
        keyboard = {
            'keyboard': [[{
                'text': '📱 Telefonimni ulashish',
                'request_contact': True,
            }]],
            'resize_keyboard': True,
            'one_time_keyboard': True,
        }
        try:
            requests.post(f"{self.base}/sendMessage", json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'Markdown',
                'reply_markup': keyboard,
            }, timeout=10)
        except Exception as e:
            log.error(f"sendMessage with contact button failed: {e}")

    def _send_remove_keyboard(self, chat_id, text):
        try:
            requests.post(f"{self.base}/sendMessage", json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'Markdown',
                'reply_markup': {'remove_keyboard': True},
            }, timeout=10)
        except Exception as e:
            log.error(f"sendMessage failed: {e}")

    def _send_with_url_button(self, chat_id, button_text, button_url, text):
        keyboard = {
            'inline_keyboard': [[{
                'text': button_text,
                'url': button_url,
            }]]
        }
        try:
            requests.post(f"{self.base}/sendMessage", json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'Markdown',
                'reply_markup': keyboard,
            }, timeout=10)
        except Exception as e:
            log.error(f"sendMessage with url button failed: {e}")

    def _send_with_kb(self, chat_id, text, keyboard):
        try:
            requests.post(f"{self.base}/sendMessage", json={
                'chat_id': chat_id,
                'text': text,
                'parse_mode': 'Markdown',
                'reply_markup': keyboard,
            }, timeout=10)
        except Exception as e:
            log.error(f"sendMessage with kb failed: {e}")

    def _edit_msg(self, chat_id, message_id, text, keyboard=None):
        payload = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'parse_mode': 'Markdown',
        }
        if keyboard is not None:
            payload['reply_markup'] = keyboard
        try:
            requests.post(f"{self.base}/editMessageText", json=payload, timeout=10)
        except Exception as e:
            log.error(f"editMessageText failed: {e}")

    def _answer_callback(self, callback_id, text=None):
        try:
            payload = {'callback_query_id': callback_id}
            if text: payload['text'] = text
            requests.post(f"{self.base}/answerCallbackQuery", json=payload, timeout=5)
        except Exception:
            pass

    def _send_document(self, chat_id, filename, file_bytes, caption=None, mime_type='application/octet-stream'):
        try:
            files = {'document': (filename, file_bytes, mime_type)}
            data = {'chat_id': chat_id}
            if caption: data['caption'] = caption
            requests.post(f"{self.base}/sendDocument", data=data, files=files, timeout=60)
        except Exception as e:
            log.error(f"sendDocument failed: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # ADMIN PANEL — inline button interface, restricted to ADMIN_IDS
    # ═══════════════════════════════════════════════════════════════════════════

    def _admin_main_kb(self):
        return {'inline_keyboard': [
            [{'text': '📊 Dashboard',     'callback_data': 'a:dash'},
             {'text': '👥 Talabalar',     'callback_data': 'a:users'}],
            [{'text': '⏳ Kutilayotgan',   'callback_data': 'a:pending'},
             {'text': "🎓 Faol a'zolar",  'callback_data': 'a:enrolled'}],
            [{'text': '📥 Excel eksport', 'callback_data': 'a:export'},
             {'text': '🔍 Qidirish',      'callback_data': 'a:search'}],
            [{'text': '📢 Broadcast',     'callback_data': 'a:broadcast'},
             {'text': '✅ Qabul qilish',   'callback_data': 'a:enroll'}],
            [{'text': '📈 Modullar',      'callback_data': 'a:modules'},
             {'text': "💰 To'lovlar",     'callback_data': 'a:payments'}],
            [{'text': '⚙️ Tizim',         'callback_data': 'a:system'},
             {'text': '❓ Yordam',         'callback_data': 'a:help'}],
        ]}

    def _kb_back(self):
        return {'inline_keyboard': [[{'text': '⬅ Orqaga', 'callback_data': 'a:dash'}]]}

    def _show_admin_menu(self, chat_id):
        self._send_with_kb(chat_id,
            "🔐 *Admin paneli*\n\nKerakli amalni tanlang:",
            self._admin_main_kb())

    # ── Callback router ──────────────────────────────────────────────────────
    def _handle_callback(self, cq):
        cq_id = cq['id']
        from_id = cq['from']['id']
        chat_id = cq['message']['chat']['id']
        msg_id = cq['message']['message_id']
        data = cq.get('data', '')

        if not _is_admin(from_id):
            self._answer_callback(cq_id, "❌ Ruxsat yo'q")
            return

        self._answer_callback(cq_id)

        if not data.startswith('a:'):
            return
        action = data[2:]

        if action == 'dash':       self._cb_dashboard(chat_id, msg_id)
        elif action == 'users':    self._cb_users(chat_id, msg_id)
        elif action == 'pending':  self._cb_pending(chat_id, msg_id)
        elif action == 'enrolled': self._cb_enrolled(chat_id, msg_id)
        elif action == 'export':   self._cb_export(chat_id, msg_id)
        elif action == 'search':   self._cb_start_search(chat_id, msg_id)
        elif action == 'enroll':   self._cb_start_enroll(chat_id, msg_id)
        elif action == 'broadcast':self._cb_start_broadcast(chat_id, msg_id)
        elif action == 'modules':  self._cb_modules(chat_id, msg_id)
        elif action == 'payments': self._cb_payments(chat_id, msg_id)
        elif action == 'system':   self._cb_system(chat_id, msg_id)
        elif action == 'help':     self._cb_help(chat_id, msg_id)
        elif action.startswith('confirm_broadcast:'):
            self._cb_send_broadcast(chat_id, msg_id, action.split(':',1)[1])
        elif action.startswith('enroll_user:'):
            try: pk = int(action.split(':',1)[1])
            except ValueError: return
            self._do_enroll(chat_id, msg_id, pk)
        elif action == 'cancel':
            _admin_state.pop(chat_id, None)
            self._cb_dashboard(chat_id, msg_id)

    # ── Stats / aggregations ─────────────────────────────────────────────────
    def _stats(self):
        from apps.payments.models import PaymentRecord
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        week_ago = now - timedelta(days=7)
        day_ago = now - timedelta(days=1)

        total = CustomUser.objects.filter(is_staff=False).count()
        enrolled = CustomUser.objects.filter(is_enrolled=True).count()
        pending = total - enrolled
        new_today = CustomUser.objects.filter(is_staff=False, created_at__gte=day_ago).count()
        new_week = CustomUser.objects.filter(is_staff=False, created_at__gte=week_ago).count()
        rev_month = PaymentRecord.objects.filter(created_at__gte=month_start).aggregate(t=Sum('amount'))['t'] or 0
        rev_total = PaymentRecord.objects.aggregate(t=Sum('amount'))['t'] or 0
        return dict(total=total, enrolled=enrolled, pending=pending,
                    new_today=new_today, new_week=new_week,
                    rev_month=rev_month, rev_total=rev_total)

    def _cb_dashboard(self, chat_id, msg_id):
        s = self._stats()
        text = (
            "📊 *Dashboard*\n\n"
            f"👥 Jami talabalar: *{s['total']}*\n"
            f"🎓 Faol (to'lagan): *{s['enrolled']}*\n"
            f"⏳ Kutilayotgan: *{s['pending']}*\n"
            "\n"
            f"🆕 Bugun ro'yxatdan: *{s['new_today']}*\n"
            f"📈 So'nggi 7 kunda: *{s['new_week']}*\n"
            "\n"
            f"💰 Bu oy daromad: *${s['rev_month']}*\n"
            f"💎 Jami daromad: *${s['rev_total']}*"
        )
        self._edit_msg(chat_id, msg_id, text, self._admin_main_kb())

    # ── Users list ───────────────────────────────────────────────────────────
    def _format_user_row(self, u, idx=None):
        prefix = f"{idx}. " if idx else "• "
        status = "🎓" if u.is_enrolled else "⏳"
        name = u.full_name or "—"
        phone = u.phone or "—"
        when = u.created_at.strftime("%Y-%m-%d %H:%M")
        return f"{prefix}{status} *{name}*\n   {phone} · @{u.telegram_username or '—'}\n   _Ro'yxat: {when}_"

    def _cb_users(self, chat_id, msg_id):
        recent = CustomUser.objects.filter(is_staff=False).order_by('-created_at')[:10]
        if not recent:
            text = "👥 *Talabalar*\n\n_Hozircha hech kim ro'yxatdan o'tmagan._"
        else:
            rows = [self._format_user_row(u, i+1) for i, u in enumerate(recent)]
            text = "👥 *So'nggi 10 talaba:*\n\n" + "\n\n".join(rows)
        self._edit_msg(chat_id, msg_id, text, self._kb_back())

    def _cb_pending(self, chat_id, msg_id):
        pending = CustomUser.objects.filter(is_staff=False, is_enrolled=False).order_by('-created_at')[:15]
        if not pending:
            text = "⏳ *Kutilayotganlar*\n\n_Yo'q. Hammasi qabul qilingan._"
        else:
            rows = [self._format_user_row(u, i+1) for i, u in enumerate(pending)]
            text = f"⏳ *Kutilayotgan {pending.count()} talaba:*\n\n" + "\n\n".join(rows)
        self._edit_msg(chat_id, msg_id, text, self._kb_back())

    def _cb_enrolled(self, chat_id, msg_id):
        enrolled = CustomUser.objects.filter(is_staff=False, is_enrolled=True).order_by('-enrolled_at')[:15]
        if not enrolled:
            text = "🎓 *Faol a'zolar*\n\n_Hali a'zolik xarid qilinmagan._"
        else:
            rows = []
            for i, u in enumerate(enrolled, 1):
                when = u.enrolled_at.strftime("%Y-%m-%d") if u.enrolled_at else "—"
                rows.append(f"{i}. 🎓 *{u.full_name or '—'}*\n   {u.phone or '—'} · _qabul: {when}_")
            text = f"🎓 *Faol a'zolar ({enrolled.count()} ko'rsatildi):*\n\n" + "\n\n".join(rows)
        self._edit_msg(chat_id, msg_id, text, self._kb_back())

    # ── Excel export ─────────────────────────────────────────────────────────
    def _cb_export(self, chat_id, msg_id):
        try:
            from openpyxl import Workbook
        except ImportError:
            self._edit_msg(chat_id, msg_id,
                "❌ Excel kutubxonasi o'rnatilmagan. Server admini openpyxl o'rnatishi kerak.",
                self._kb_back())
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Talabalar"
        ws.append(["#", "Ism", "Telefon", "Telegram", "Email", "Status",
                   "Ro'yxat sanasi", "Qabul sanasi", "Tugatildi (dars)"])

        from apps.courses.models import LessonProgress
        users = CustomUser.objects.filter(is_staff=False).order_by('-created_at')
        for i, u in enumerate(users, 1):
            completed = LessonProgress.objects.filter(user=u, completed=True).count()
            ws.append([
                i,
                u.full_name or "",
                u.phone or "",
                f"@{u.telegram_username}" if u.telegram_username else "",
                u.email or "",
                "Qabul qilingan" if u.is_enrolled else "Kutilmoqda",
                u.created_at.strftime("%Y-%m-%d %H:%M"),
                u.enrolled_at.strftime("%Y-%m-%d") if u.enrolled_at else "",
                completed,
            ])

        # Make header bold + freeze first row
        from openpyxl.styles import Font, Alignment
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="left")
        ws.freeze_panes = "A2"
        # Reasonable column widths
        for col, w in zip("ABCDEFGHI", [4, 28, 18, 18, 28, 16, 18, 14, 10]):
            ws.column_dimensions[col].width = w

        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)

        filename = f"talabalar-{timezone.now().strftime('%Y%m%d-%H%M')}.xlsx"
        self._send_document(chat_id, filename, buf.getvalue(),
            caption=f"📊 {users.count()} talaba ro'yxati",
            mime_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self._edit_msg(chat_id, msg_id,
            f"📥 *Excel eksport tayyor*\n\n{users.count()} talaba yuborildi.",
            self._kb_back())

    # ── Module / payment / system pages ──────────────────────────────────────
    def _cb_modules(self, chat_id, msg_id):
        from apps.courses.models import Module, LessonProgress
        total_users = CustomUser.objects.filter(is_enrolled=True).count() or 1
        rows = []
        for m in Module.objects.prefetch_related('lessons').order_by('order')[:14]:
            real = m.lessons.count()
            if real == 0:
                rows.append(f"• M{m.order:02d} {m.title} — _bo'sh_")
                continue
            done = LessonProgress.objects.filter(
                lesson__module=m, completed=True
            ).count()
            pct = round(100 * done / (total_users * real)) if real else 0
            rows.append(f"• M{m.order:02d} {m.title} — {real} video · {pct}% tugatildi")
        text = "📈 *Modullar bo'yicha statistika:*\n\n" + "\n".join(rows) if rows else "Modullar yo'q"
        self._edit_msg(chat_id, msg_id, text, self._kb_back())

    def _cb_payments(self, chat_id, msg_id):
        from apps.payments.models import PaymentRecord
        recent = PaymentRecord.objects.select_related('user').order_by('-created_at')[:10]
        if not recent:
            text = "💰 *To'lovlar*\n\n_Hali hech qanday to'lov yozilmagan._"
        else:
            rows = []
            for r in recent:
                name = r.user.full_name or "—"
                rows.append(f"• ${r.amount} — *{name}* · _{r.payment_date}_\n  {r.payment_reference[:40]}")
            text = "💰 *So'nggi to'lovlar:*\n\n" + "\n\n".join(rows)
        self._edit_msg(chat_id, msg_id, text, self._kb_back())

    def _cb_system(self, chat_id, msg_id):
        import platform
        try:
            import psutil
            mem = psutil.virtual_memory()
            mem_str = f"{mem.percent}% (free {round(mem.available/1024/1024)} MB)"
            disk = psutil.disk_usage('/')
            disk_str = f"{disk.percent}% (free {round(disk.free/1024/1024/1024,1)} GB)"
            load = ', '.join(f"{x:.2f}" for x in os.getloadavg()[:3])
        except Exception:
            mem_str = disk_str = load = "—"
        text = (
            "⚙️ *Tizim*\n\n"
            f"🌐 Site: {SITE_URL}\n"
            f"🐍 Python: {platform.python_version()}\n"
            f"💾 Disk: {disk_str}\n"
            f"🧠 RAM: {mem_str}\n"
            f"📊 Load avg: {load}\n"
            f"👮 Adminlar: {len(ADMIN_IDS)}"
        )
        self._edit_msg(chat_id, msg_id, text, self._kb_back())

    def _cb_help(self, chat_id, msg_id):
        text = (
            "❓ *Yordam*\n\n"
            "Buyruqlar:\n"
            "• /admin — admin panelni ochish\n"
            "• /stats — tezkor dashboard\n"
            "• /cancel — joriy amalni bekor qilish\n\n"
            "Tugmalar:\n"
            "📊 *Dashboard* — umumiy statistika\n"
            "👥 *Talabalar* — so'nggi ro'yxatdan o'tganlar\n"
            "⏳ *Kutilayotgan* — to'lov tasdiqlanmaganlar\n"
            "🎓 *Faol a'zolar* — to'lagan talabalar\n"
            "📥 *Excel eksport* — barcha talabalarning XLSX fayli\n"
            "🔍 *Qidirish* — ism/telefon bo'yicha qidirish\n"
            "📢 *Broadcast* — barcha talabalarga xabar yuborish\n"
            "✅ *Qabul qilish* — telefon/email orqali tezkor qabul\n"
            "📈 *Modullar* — modullar bo'yicha statistika\n"
            "💰 *To'lovlar* — so'nggi to'lovlar tarixi\n"
            "⚙️ *Tizim* — server holati"
        )
        self._edit_msg(chat_id, msg_id, text, self._kb_back())

    # ── Search / Enroll / Broadcast — multi-step state machine ──────────────
    def _cb_start_search(self, chat_id, msg_id):
        _admin_state[chat_id] = ('search', {})
        self._edit_msg(chat_id, msg_id,
            "🔍 *Qidirish*\n\nIsm, telefon yoki @username yuboring.\n_Bekor qilish: /cancel_",
            self._kb_back())

    def _cb_start_enroll(self, chat_id, msg_id):
        _admin_state[chat_id] = ('enroll', {})
        self._edit_msg(chat_id, msg_id,
            "✅ *Qabul qilish*\n\nQabul qilinadigan talaba topish uchun "
            "ism, telefon yoki @username yuboring.\n_Bekor qilish: /cancel_",
            self._kb_back())

    def _cb_start_broadcast(self, chat_id, msg_id):
        _admin_state[chat_id] = ('broadcast_text', {})
        self._edit_msg(chat_id, msg_id,
            "📢 *Broadcast*\n\nYubormoqchi bo'lgan matnni yozing.\n"
            "_Faqat faol a'zolarga yuboriladi._\n_Bekor qilish: /cancel_",
            self._kb_back())

    def _handle_admin_input(self, chat_id, tg_id, text, mode, payload):
        from django.db.models import Q
        if mode in ('search', 'enroll'):
            q = text.strip().lstrip('@')
            matches = CustomUser.objects.filter(is_staff=False).filter(
                Q(full_name__icontains=q) | Q(phone__icontains=q) |
                Q(telegram_username__icontains=q) | Q(email__icontains=q)
            )[:8]
            _admin_state.pop(chat_id, None)
            if not matches:
                self._send(chat_id, f"❌ '{q}' bo'yicha hech narsa topilmadi.")
                return
            if mode == 'enroll':
                # Inline-enroll buttons for each match
                kb_rows = [
                    [{'text': f"✅ {u.full_name or u.phone}", 'callback_data': f"a:enroll_user:{u.pk}"}]
                    for u in matches
                ]
                kb_rows.append([{'text': '⬅ Orqaga', 'callback_data': 'a:dash'}])
                rows = [self._format_user_row(u) for u in matches]
                self._send_with_kb(chat_id,
                    "✅ *Kimni qabul qilamiz?*\n\n" + "\n\n".join(rows),
                    {'inline_keyboard': kb_rows})
                return
            # Just search — show results
            rows = [self._format_user_row(u, i+1) for i, u in enumerate(matches)]
            extra = f"\n\n_(+{matches.count()-8} ko'proq mos)_" if len(matches) >= 8 else ""
            self._send(chat_id, "🔍 *Topilganlar:*\n\n" + "\n\n".join(rows) + extra)
            return

        if mode == 'broadcast_text':
            _admin_state[chat_id] = ('broadcast_confirm', {'text': text})
            target_count = CustomUser.objects.filter(is_enrolled=True).count()
            kb = {'inline_keyboard': [[
                {'text': f"✅ Yuborish ({target_count} ta)", 'callback_data': 'a:confirm_broadcast:enrolled'},
                {'text': '❌ Bekor',                       'callback_data': 'a:cancel'},
            ]]}
            self._send_with_kb(chat_id,
                f"📢 *Broadcast tasdiqlash*\n\nMatn:\n\n{text}\n\n"
                f"🎯 {target_count} ta faol a'zoga yuboriladi.",
                kb)
            return

    def _cb_send_broadcast(self, chat_id, msg_id, target):
        mode, payload = _admin_state.get(chat_id, (None, {}))
        text = payload.get('text', '') if mode == 'broadcast_confirm' else ''
        _admin_state.pop(chat_id, None)
        if not text:
            self._edit_msg(chat_id, msg_id, "❌ Matn topilmadi.", self._kb_back())
            return
        if target == 'enrolled':
            users = CustomUser.objects.filter(is_enrolled=True, telegram_id__isnull=False)
        else:
            users = CustomUser.objects.filter(telegram_id__isnull=False, is_staff=False)
        sent = 0
        failed = 0
        for u in users:
            try:
                requests.post(f"{self.base}/sendMessage", json={
                    'chat_id': u.telegram_id, 'text': text, 'parse_mode': 'Markdown',
                }, timeout=10)
                sent += 1
                time.sleep(0.05)  # gentle rate-limit
            except Exception:
                failed += 1
        self._edit_msg(chat_id, msg_id,
            f"📢 *Broadcast tugadi*\n\n✅ Yuborildi: {sent}\n❌ Xatolar: {failed}",
            self._kb_back())

    # Quick-enroll callback (when admin taps a user button from enroll search)
    # callback_data format: "a:enroll_user:<pk>"
    def _do_enroll(self, chat_id, msg_id, pk):
        from apps.payments.models import PaymentRecord
        try:
            u = CustomUser.objects.get(pk=pk)
        except CustomUser.DoesNotExist:
            self._edit_msg(chat_id, msg_id, "❌ Talaba topilmadi.", self._kb_back())
            return
        if u.is_enrolled:
            self._edit_msg(chat_id, msg_id,
                f"ℹ️ *{u.display_name}* allaqachon qabul qilingan.", self._kb_back())
            return
        u.is_enrolled = True
        u.enrolled_at = timezone.now()
        u.save()
        # Record a placeholder payment so the dashboard counts it
        PaymentRecord.objects.create(
            user=u, amount=300, payment_reference="Telegram bot enroll",
            payment_date=timezone.now().date(),
        )
        # Notify the user
        if u.telegram_id:
            try:
                requests.post(f"{self.base}/sendMessage", json={
                    'chat_id': u.telegram_id,
                    'text': "🎉 *Kursga qabul qilindingiz!*\n\n"
                            "Endi siz barcha modullarga kira olasiz.\n"
                            f"Saytga kiring: {SITE_URL}/kurs/",
                    'parse_mode': 'Markdown',
                }, timeout=10)
            except Exception: pass
        self._edit_msg(chat_id, msg_id,
            f"✅ *{u.display_name}* kursga qabul qilindi.",
            self._kb_back())
