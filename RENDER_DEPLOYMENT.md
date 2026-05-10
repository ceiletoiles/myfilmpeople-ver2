# MyFilmPeople - Render Deployment Guide

This guide walks you through deploying MyFilmPeople to [Render.com](https://render.com).

## Overview

This deployment includes:
- **Django Web Service** - Hosted on Render with Gunicorn
- **PostgreSQL Database** - Managed by Render
- **Redis Cache** - Hosted on Render
- **Static Files** - Served via WhiteNoise
- **Environment Variables** - Managed through Render dashboard

## Prerequisites

1. **GitHub Repository** - Push your code to GitHub (Render pulls from GitHub)
2. **Render Account** - Sign up at [render.com](https://render.com)
3. **TMDb API Keys** - Have your TMDB_API_KEY and TMDB_API_READ_ACCESS_TOKEN ready

## Step 1: Prepare Your Repository

1. Commit all changes:
   ```bash
   git add .
   git commit -m "Prepare for Render deployment"
   git push origin main
   ```

2. Ensure these files exist (they should already be created):
   - `requirements.txt` - Updated with deployment packages
   - `build.sh` - Build script for Render
   - `render.yaml` - Infrastructure configuration
   - `Procfile` - Already exists with Gunicorn config
   - `config/settings.py` - Updated for Render support

## Step 2: Create Services on Render

### Option A: Using render.yaml (Recommended)

1. Go to [Render Dashboard](https://dashboard.render.com)
2. Click **New +** → **Blueprint**
3. Connect your GitHub repository
4. Select the repository branch (main)
5. Provide a name for your blueprint (e.g., "MyFilmPeople")
6. Click **Create Blueprint**
7. Render will parse `render.yaml` and create:
   - Web service
   - Redis cache
   - PostgreSQL database (you'll add this)

### Option B: Manual Service Creation

#### 2.1 Create PostgreSQL Database

1. In Render Dashboard, click **New +** → **PostgreSQL**
2. Fill in the details:
   - **Name**: `myfilmpeople-db`
   - **Database**: `myfilmpeople`
   - **User**: `myfilmpeople`
   - **Region**: Choose the same region as your web service
   - **Plan**: Standard ($15/month) - minimum recommended
3. Click **Create Database**
4. Wait for the database to be created (2-3 minutes)
5. Copy the **Internal Database URL** - you'll use this for the web service

#### 2.2 Create Redis Cache

1. Click **New +** → **Redis**
2. Fill in the details:
   - **Name**: `myfilmpeople-redis`
   - **Region**: Same region as web service
   - **Plan**: Standard ($15/month)
3. Click **Create Redis**
4. Wait for Redis to be created
5. Copy the **Internal Redis URL** - format: `redis://:password@host:port`

#### 2.3 Create Web Service

1. Click **New +** → **Web Service**
2. Connect your GitHub repository
3. Select your MyFilmPeople repository
4. Fill in the details:
   - **Name**: `myfilmpeople-web`
   - **Runtime**: Python
   - **Build Command**: `bash build.sh`
   - **Start Command**: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --log-level info`
   - **Plan**: Standard ($12/month) or higher

5. Click **Create Web Service**

## Step 3: Configure Environment Variables

In Render Dashboard, go to your web service and click **Environment**:

```
SECRET_KEY=<generate-a-secure-random-string>
DEBUG=0
ALLOWED_HOSTS=yourdomain.render.com

DATABASE_URL=<paste-the-PostgreSQL-internal-URL>

REDIS_URL=<paste-the-Redis-internal-URL>
REDIS_KEY_PREFIX=myfilmpeople

TMDB_API_KEY=<your-tmdb-api-key>
TMDB_API_READ_ACCESS_TOKEN=<your-tmdb-read-access-token>
TMDB_LANGUAGE=en-US
TMDB_REGION=

SECURE_SSL_REDIRECT=1
SESSION_COOKIE_SECURE=1
CSRF_COOKIE_SECURE=1
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=1
SECURE_HSTS_PRELOAD=1
```

### How to generate SECRET_KEY:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Or use this in a Python shell:
```python
import secrets
import string
alphabet = string.ascii_letters + string.digits + string.punctuation
secret = ''.join(secrets.choice(alphabet) for i in range(50))
print(secret)
```

## Step 4: Link Services Together

In Render Dashboard:

1. **Web Service** needs to know about **PostgreSQL** and **Redis**:
   - Open your web service → **Environment**
   - The DATABASE_URL and REDIS_URL from Step 3 should reference the PostgreSQL and Redis services
   - Click **Save**

2. Render will automatically link services created from the same blueprint

## Step 5: Deploy

### First Deployment

1. After setting environment variables, click **Deploy** on your web service
2. Watch the deployment logs:
   - Python dependencies install
   - Database migrations run
   - Static files are collected
   - Gunicorn starts
3. Once the status shows **Live** (green), your app is running!

### Accessing Your App

- Your app will be available at: `https://myfilmpeople-web.onrender.com` (or whatever name you chose)
- Render provides a `.onrender.com` subdomain for free
- You can add a custom domain in **Settings** → **Custom Domain**

## Step 6: Verify Everything Works

1. Visit your app URL
2. Log in with your admin account (you might need to create a superuser - see below)
3. Test the following:
   - Home page loads
   - Authentication works
   - Search functionality
   - Movie/person pages load
   - Redis caching (check logs for cache hits)
   - Static files load (CSS, JS, images)

### Create a Superuser for Production

If you need to create an admin account:

1. In Render Dashboard, open your web service
2. Click **Shell** tab at the top
3. Run:
   ```bash
   python manage.py createsuperuser
   ```
4. Follow the prompts to create an admin account
5. Access admin at: `https://yourdomain.onrender.com/admin`

## Step 7: Continuous Deployment

Once set up, Render will automatically redeploy when you:
1. Push to your main branch
2. Click **Deploy** manually in the dashboard

To disable auto-deploy:
- Go to your web service → **Settings** → **Auto-Deploy** → Toggle off

## Monitoring & Logs

1. **View Logs**: Click **Logs** tab in your web service
2. **Monitor Performance**: Render provides metrics in the **Metrics** tab
3. **Database Logs**: Check PostgreSQL service logs for database issues

## Troubleshooting

### "ModuleNotFoundError: No module named 'django'"

- Check that `requirements.txt` has all dependencies
- Verify `build.sh` runs pip install

### "ALLOWED_HOSTS error"

- Set `ALLOWED_HOSTS` environment variable to your render domain
- Example: `yourdomain.onrender.com`

### Static files not loading (404 errors)

- Ensure `collectstatic` ran in build.sh
- Check WhiteNoise is in middleware
- View logs for collection errors

### Database connection refused

- Verify DATABASE_URL is correct (internal URL for same region)
- Check PostgreSQL service is running
- Wait 2-3 minutes after creating PostgreSQL for it to be ready

### Redis connection refused

- Verify REDIS_URL is correct
- Check Redis service is running
- Note: Redis is optional; app works without it (but caching will be disabled)

### Gunicorn errors at startup

- Check application logs for stack traces
- Verify all environment variables are set
- Run migrations: `python manage.py migrate`

## Database Backups

Render provides automated daily backups for PostgreSQL:

1. Go to your PostgreSQL service in Render
2. Click **Backups** tab
3. Download manual backup if needed
4. Backups are retained for 14 days

## Scaling & Upgrades

As traffic grows:

1. **Web Service**: Upgrade plan or increase instance count
2. **Database**: Render allows easy plan upgrades
3. **Redis**: Upgrade plan if cache is full

## Costs

On Render's free tier:
- Basic web: $0 (sleeps after 15 min inactivity)
- Standard deployment: ~$42/month (Web $12 + DB $15 + Redis $15)

## Next Steps

1. Set up custom domain (optional)
2. Configure email for password resets
3. Set up monitoring/alerts
4. Configure CDN for media files (optional)
5. Set up database backups/restoration workflow

## Support

- **Render Docs**: https://render.com/docs
- **Django Deployment**: https://docs.djangoproject.com/en/5.1/howto/deployment/
- **Render Support**: support@render.com

---

**Happy deploying! 🚀**
