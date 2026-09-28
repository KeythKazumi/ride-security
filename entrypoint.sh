#!/bin/sh
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "Running migrations..."
python manage.py migrate --noinput

echo "Collecting static files..."
python manage.py collectstatic --noinput --clear

echo "Running setup..."
python manage.py setup --no-sample-data

echo "Starting server..."
exec python manage.py runserver 0.0.0.0:8000

