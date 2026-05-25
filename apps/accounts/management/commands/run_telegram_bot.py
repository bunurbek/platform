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
import requests
from django.core.management.base import BaseCommand
from django.conf import settings

from apps.accounts.models import TelegramAuthSession, CustomUser

logging.basicConfig(
    format='%(asctime)s — %(levelname)s — %(message)s',
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# Public URL where the website is reachable for the auth link.
# In dev: localhost. In production: real domain.
SITE_URL = getattr(settings, 'SITE_URL', 'http://127.0.0.1:8000')


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
                            'allowed_updates': '["message"]'},
                    timeout=15,
                )
                data = resp.json()
                if not data.get('ok'):
                    log.error(f"Telegram API error: {data}")
                    time.sleep(3)
                    continue

                for update in data.get('result', []):
                    offset = update['update_id'] + 1
                    try:
                        self._handle_update(update)
                    except Exception as e:
                        log.exception(f"Update error: {e}")

            except requests.exceptions.Timeout:
                continue
            except KeyboardInterrupt:
                self.stdout.write("\nBot stopped.")
                break
            except Exception as e:
                log.error(f"Polling error: {e}")
                time.sleep(3)

    # ── Update routing ────────────────────────────────────────────────────────

    def _handle_update(self, update):
        msg = update.get('message')
        if not msg:
            return

        chat_id = msg['chat']['id']
        tg_user = msg.get('from', {})
        tg_id   = tg_user.get('id')
        text    = (msg.get('text') or '').strip()
        contact = msg.get('contact')

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
