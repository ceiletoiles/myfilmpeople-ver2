Deployment checklist and commands

1) Prepare environment
- Ensure environment variables are set (use .env on staging only for convenience). See .env.example.
- Ensure SECRET_KEY, ALLOWED_HOSTS, DB_*, REDIS_URL, TMDB_API_KEY are configured.

2) Apply database migrations
```bash
python manage.py migrate --noinput
```

3) Collect static files
```bash
python manage.py collectstatic --noinput
```

4) Restart application (systemd / process manager)
- Example systemd: `sudo systemctl restart gunicorn`
- Container: rebuild image and restart service

5) Smoke test (quick):
- Visit homepage and a few detail pages
- Run `curl --fail -I https://example.com/` to confirm 200/301
- Check logs: `journalctl -u gunicorn -n 200 --no-pager` or container logs

6) Backup DB before migration (MySQL example):
```bash
mysqldump -u $DB_USER -p$DB_PASSWORD -h $DB_HOST $DB_NAME > /backups/myfilmpeople-$(date +%Y%m%d-%H%M).sql
```

7) Rollback plan
- Restore DB: `mysql -u $DB_USER -p$DB_PASSWORD -h $DB_HOST $DB_NAME < /backups/previous.sql`
- Re-deploy previous release (from Git tag or previous image)
- If using migrations that are not reversible, have an export of important tables and manual remediation steps documented

8) Monitoring & alerts
- Add error/log aggregation (Sentry/LogDNA/ELK) and create alerts for 5xx spikes and background job failures
- Ensure uptime checks and health endpoints are monitored

9) Post-deploy
- Run smoke tests and sanity checks (search, person page, login)
- Verify background jobs (if any) are scheduled and running

Notes
- For zero-downtime in production, use a graceful reload strategy for the app server and run DB migrations in a way that is compatible with older code (expand/contract pattern).

Continuous integration
- A GitHub Actions CI workflow is included at `.github/workflows/ci.yml`.
- CI runs: install, migrations, collectstatic, `ruff` lint, and tests using MySQL+Redis services.
- The `Makefile` provides convenience targets: `make install`, `make lint`, `make test`, and `make collectstatic`.
