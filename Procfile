release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: gunicorn config.wsgi --workers 2 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT --log-file -
worker: python manage.py run_telegram_bot
