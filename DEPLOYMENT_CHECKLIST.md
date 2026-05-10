# Render Deployment Checklist

## Pre-Deployment Setup ✅

- [ ] **requirements.txt** - Updated with: gunicorn, dj-database-url, whitenoise, psycopg2-binary
- [ ] **build.sh** - Created (collectstatic, migrate)
- [ ] **render.yaml** - Created (optional, for Infrastructure as Code)
- [ ] **config/settings.py** - Updated:
  - [ ] Added `dj_database_url` import
  - [ ] Added WhiteNoise middleware
  - [ ] Updated DATABASE configuration (PostgreSQL support)
  - [ ] Added `RENDER_EXTERNAL_HOSTNAME` to ALLOWED_HOSTS
  - [ ] Added `STATICFILES_STORAGE` for WhiteNoise
  - [ ] Added proxy headers for HTTPS
- [ ] **GitHub** - All code pushed to main branch

## Render Service Setup ✅

### PostgreSQL Database
- [ ] Create PostgreSQL on Render
- [ ] Copy Internal Database URL
- [ ] Note: Render auto-creates database

### Redis Cache
- [ ] Create Redis on Render
- [ ] Copy Internal Redis URL

### Web Service
- [ ] Create Web Service on Render
- [ ] Connect GitHub repository
- [ ] Set Build Command: `bash build.sh`
- [ ] Set Start Command: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --log-level info`

## Environment Variables Setup ✅

Configure in Render Dashboard (Web Service → Environment):

- [ ] `SECRET_KEY` - Generate a secure random string
- [ ] `DEBUG` - Set to `0` (production)
- [ ] `ALLOWED_HOSTS` - Set to your Render domain
- [ ] `DATABASE_URL` - PostgreSQL internal URL
- [ ] `REDIS_URL` - Redis internal URL
- [ ] `REDIS_KEY_PREFIX` - `myfilmpeople`
- [ ] `TMDB_API_KEY` - Your TMDb API key
- [ ] `TMDB_API_READ_ACCESS_TOKEN` - Your TMDb token
- [ ] Security settings:
  - [ ] `SECURE_SSL_REDIRECT` - `1`
  - [ ] `SESSION_COOKIE_SECURE` - `1`
  - [ ] `CSRF_COOKIE_SECURE` - `1`

## First Deployment ✅

- [ ] Click **Deploy** on web service
- [ ] Monitor logs (should see migrations running)
- [ ] Wait for status to show **Live** (green)
- [ ] Visit `https://yourdomain.onrender.com`
- [ ] Verify home page loads with styling

## Post-Deployment Verification ✅

- [ ] Website loads without errors
- [ ] Static files load (CSS, JS, images visible)
- [ ] Authentication works (try logging in)
- [ ] Admin panel accessible at `/admin`
- [ ] Create superuser if needed (via Shell tab)
- [ ] Test main features:
  - [ ] Search functionality
  - [ ] Movie detail pages
  - [ ] Person detail pages
  - [ ] Follow/unfollow works
- [ ] Check logs for errors: Render Dashboard → Logs
- [ ] Verify Redis is connected: Check cache headers in responses

## Ongoing Maintenance ✅

- [ ] Monitor logs regularly
- [ ] Check database backups are running
- [ ] Monitor disk usage
- [ ] Update dependencies periodically
- [ ] Test database restores occasionally

---

**Deployment Status**: Use this checklist to track progress during deployment.
