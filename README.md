# Bu Nurbek — Cinematic Videography Course Platform

Django-based course platform for Uzbek-speaking videography students. Single $300 one-time payment confirmed manually via Telegram. Telegram bot handles passwordless authentication.

## ✨ Features

- **Uzbek landing page** — hero, instructor portfolio (Instagram embeds), real student videos & testimonials, FAQ
- **Telegram bot auth** — passwordless: user shares phone + name in bot, clicks link, logged in
- **14-module course** — `Course → Module → Lesson` hierarchy with sub-lessons
- **24h cooldown** between modules — forces spaced learning (live countdown)
- **Progressive unlock** — Module N+1 opens only after Module N is complete + 24h
- **Per-lesson homework PDF** — download button + send-via-Telegram button
- **Custom admin dashboard** — `/dashboard/` — students, payments, content manager, analytics
- **App-mode** for logged-in users — distinct nav, profile dropdown, "Continue lesson" CTA
- **Mobile-first UX** — hamburger drawer, bottom sheet lesson navigator, smart sticky CTAs

## 🛠 Stack

- **Backend:** Django 5+ / Python 3.12
- **Database:** PostgreSQL (production), SQLite (dev)
- **Storage:** Cloudflare R2 (production), local `/media/` (dev)
- **Static:** WhiteNoise (compressed manifest)
- **Frontend:** Vanilla HTML + Alpine.js + custom CSS (no build step)
- **Telegram bot:** raw HTTP long-polling (no library dependencies)

## 🚀 Local Setup

```bash
# 1. Clone + venv
git clone https://github.com/bunurbek/platform.git
cd platform
python3 -m venv .venv
source .venv/bin/activate

# 2. Install
pip install -r requirements.txt

# 3. Env vars
cp .env.example .env
# Edit .env — at minimum set TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME

# 4. Database + admin
python manage.py migrate
python manage.py createsuperuser   # use admin@bunurbek.uz / admin123 if you want
python manage.py collectstatic --noinput

# 5. Run
python manage.py runserver               # web
python manage.py run_telegram_bot        # separate terminal — Telegram bot worker
```

Open http://127.0.0.1:8000

## 🌍 Production Deploy (DigitalOcean App Platform)

### One-time setup

1. **Create app** at https://cloud.digitalocean.com/apps → "Create from GitHub" → connect `bunurbek/platform`
2. DO will detect `.do/app.yaml` automatically — review and edit secrets:
   - `SECRET_KEY` — paste 50 random chars (`python -c "import secrets; print(secrets.token_urlsafe(50))"`)
   - `TELEGRAM_BOT_TOKEN` — paste your BotFather token
3. Click **Create Resources** — DO provisions web + worker + PostgreSQL DB
4. Wait ~3 minutes for first deploy

### After it's live

- Default URL: `https://bunurbek-platform-xxxxx.ondigitalocean.app`
- To add custom domain (e.g. `bunurbek.uz`):
  - In Cloudflare DNS → add CNAME pointing to DO URL
  - In DO App → Settings → Domains → add `bunurbek.uz`
  - DO handles HTTPS automatically (Let's Encrypt)

### Cloudflare R2 for videos & PDFs (recommended)

1. Cloudflare dashboard → R2 → Create bucket `bunurbek-media`
2. Settings → "Public Access" → Allow Access (gives you a public URL like `pub-xxx.r2.dev`)
3. Create API token: R2 → Manage R2 API Tokens → Object Read & Write for this bucket
4. In DO App → Settings → environment variables:
   - `USE_R2=True`
   - `R2_ACCESS_KEY_ID=...`
   - `R2_SECRET_ACCESS_KEY=...`
   - `R2_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com`
   - `R2_PUBLIC_URL=https://pub-<hash>.r2.dev`
5. Redeploy. Future uploads go to R2; existing local files stay where they are.

## 📁 Project Structure

```
.
├── apps/
│   ├── accounts/        # CustomUser + TelegramAuthSession + bot management command
│   ├── courses/         # Course → Module → Lesson + LessonProgress
│   ├── pages/           # Landing page view (redirects logged-in users)
│   ├── payments/        # PaymentRecord (admin-entered)
│   └── dashboard/       # Custom admin (students, payments, content, analytics)
├── config/              # Django settings, urls, wsgi
├── static/
│   ├── css/             # main.css, app.css, course.css, dashboard.css
│   ├── js/              # main.js (animations, smart sticky CTA)
│   └── images/          # student work videos, photos, instructor portrait
├── templates/
│   ├── base.html        # Marketing / unauthenticated
│   ├── app_base.html    # App shell for logged-in students (slim nav + profile dropdown)
│   ├── landing/         # index.html (hero, testimonials, FAQ)
│   ├── accounts/        # tg_start, tg_waiting, login, profile
│   ├── courses/         # course_home, lesson, free_lesson, lesson_locked
│   └── dashboard/       # base, home, students, student_detail, payments, content, analytics
├── manage.py
├── requirements.txt
├── runtime.txt
├── Procfile
├── .do/app.yaml         # DigitalOcean App Platform spec
└── .env.example
```

## 🔐 Security Checklist

- [ ] `SECRET_KEY` rotated and stored in DO secrets (never committed)
- [ ] `TELEGRAM_BOT_TOKEN` stored in DO secrets
- [ ] `DEBUG=False` in production
- [ ] `ALLOWED_HOSTS` set to your actual domain only
- [ ] HTTPS enforced (`SECURE_SSL_REDIRECT=True`, handled by DO)
- [ ] PostgreSQL connection uses SSL (`ssl_require=True` in settings)
- [ ] Cloudflare R2 bucket has correct access policy (public read for media, private write)
- [ ] Telegram bot session expiry = 10 min (anti-replay)

## 📊 Admin

- Login: `/login/` (email + password)
- Custom dashboard: `/dashboard/`
- Built-in Django admin: `/admin/`

## 📞 Payments

Manual via Telegram → `t.me/bu_nurbek`. Admin records the payment in `/dashboard/students/` → student gets enrolled instantly.

## 🤖 Telegram Bot

- Token in `TELEGRAM_BOT_TOKEN` env var
- Bot username: `@bunurbekauth_bot` (from BotFather)
- Flow: `/start <token>` → bot asks for phone (Share Contact) → asks for name → sends auth-link button → user clicks → logged in on website

## 📜 License

Private — © 2026 Nurbek Bahodirov.
