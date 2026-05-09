# Release Notes — {{version}}

Date: {{date}}

Summary
- One-line summary of this release.

Changes
- List of notable changes, migrations, and upgrades.

Database migrations
- `python manage.py migrate --noinput`
- Backups: location and timestamp of DB backup used before migration

Deployment steps taken
- Collected static: `python manage.py collectstatic --noinput`
- Services restarted: e.g., `systemctl restart gunicorn`

Verification / smoke tests
- Host: {{staging_host}}
- Smoke test results: (paste output from `deploy/smoke_test.sh`)

Rollbacks
- If issues: restore DB from backup and redeploy previous release (tag/image)

Notes
- Any post-deploy follow-ups, monitoring added, tickets created
