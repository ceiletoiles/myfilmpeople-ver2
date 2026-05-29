web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --worker-class gthread --timeout 60 --max-requests 1000 --max-requests-jitter 100 --log-level info
