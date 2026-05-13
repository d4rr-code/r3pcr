#!/bin/sh
python manage.py migrate --noinput
python manage.py seed_hscodes
exec gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
