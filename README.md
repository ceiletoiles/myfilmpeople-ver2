# MyFilmPeople — People‑first Film Discovery (Updated)

MyFilmPeople is a server-rendered Django application that centers discovery around film people (actors, directors, crew) and production companies instead of only movie titles. Follow people and companies to surface their filmographies, collaborations, upcoming releases, and change/arrival notifications.

This README is an expanded, practical guide covering features, architecture, development setup (Windows-focused), common workflows, and useful management commands.

**Status:** actively maintained demo / small production-ready app (uses TMDb API and MySQL)

**Repository layout (high level)**
- [config](config): Django project settings and URL routing
- [accounts](accounts): authentication, signup, profile UI
- [catalog](catalog): core domain models, TMDb client, services, views, and management commands
- templates, static, media: UI assets and server-rendered templates

**Primary goals**
- Make it easy to follow people and studios and get personalized discovery
- Record and notify users about new movie arrivals or metadata updates for followed entities
- Offer collaboration discovery (shared film credits) with role-aware grouping

Contents
- **Features**
- **Tech stack & dependencies**
- **Development setup (Windows)**
- **Environment / .env example**
- **Database & migrations**
- **Key runtime behaviour & caching**
- **Management commands & developer workflows**
- **Testing, deployment & troubleshooting**
- **Where to look in the codebase**

**Features (user-facing)**
- Follow people with a role (Actor, Director, Writer, etc.) and follow companies/studios
- Home page grouped by role categories with quick access to followed entities
- Search that prefers DB-cached results and will top-up from TMDb when needed
- Person / Company / Movie detail pages (cached payloads for followed entities)
- Collaboration Finder: pick multiple people and see shared movies + per-person roles
- Upcoming page: aggregated upcoming releases for people and companies you follow
- Notification-style "New Movie Arrivals" when a followed entity has new movies or metadata updates
- TMDb JSON proxy endpoint for safe server-side forwarding of GET requests to TMDb

Tech stack & main dependencies
- Python 3.11+ (test with the virtualenv in this repo)
- Django 5.1.6
- MySQL (utf8mb4) using PyMySQL driver
- Requests for TMDb API calls
- python-dotenv for loading a local `.env`
- Optional Redis caching via `django-redis`
- Pillow for image handling

See [requirements.txt](requirements.txt) for pinned versions.

Development setup (Windows example)
1. Create and activate a virtual environment

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Create a `.env` file at the project root (next to `manage.py`) and populate values (example below).

3. Create the MySQL database and user. Example SQL:

```sql
CREATE DATABASE myfilmpeople CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- then create/grant a user or use your MySQL root account
```

4. Run migrations and create a superuser

```powershell
python manage.py migrate
python manage.py createsuperuser
```

5. Start the development server

```powershell
python manage.py runserver
```

Environment variables (.env example)
- SECRET_KEY=your-secret-key
- DEBUG=1
- ALLOWED_HOSTS=localhost,127.0.0.1
- DB_NAME=myfilmpeople
- DB_USER=myuser
- DB_PASSWORD=password
- DB_HOST=127.0.0.1
- DB_PORT=3306
- TMDB_API_KEY=your_tmdb_api_key
- TMDB_API_READ_ACCESS_TOKEN=your_tmdb_read_token
- TMDB_LANGUAGE=en-US
- REDIS_URL=redis://127.0.0.1:6379/1
- REDIS_KEY_PREFIX=myfilmpeople

Key configuration notes
- [config/settings.py](config/settings.py) reads `.env` via python-dotenv.
- TMDb caching TTL and person comeback/inactivity thresholds are configurable via environment variables `TMDB_CACHE_TTL_HOURS`, `TMDB_PERSON_COMEBACK_THRESHOLD_YEARS`, and `TMDB_PERSON_INACTIVE_THRESHOLD_YEARS`.
- Optional `CORS_PROXIES` environment variable provides fallback proxy prefixes for environments where direct TMDb requests may be blocked.

Database & caching behaviour
- The app stores cached TMDb payloads for followed people and companies in JSON fields on the corresponding models. These cached payloads are refreshed during sync operations (manual or scheduled).
- Redis is used as Django's cache backend when configured. The cache is treated as an optimization and the app is resilient to Redis failures (caching failures are ignored and treated as cache-misses).

Common management commands (developer-focused)
- `python manage.py migrate` — apply migrations
- `python manage.py createsuperuser` — create admin user
- `python manage.py test` — run tests

Management commands under `catalog/management/commands/` (examples):
- `demo_new_arrivals_update` — demo tool: temporarily wipes a cached release_date for a person/company, forces a sync, then records a NewMovieArrival event for a user (useful to test notifications)
- `demo_comeback_arrival` — similar demo for comeback detection (if present)
- `publish_dailies_due` — scheduled job helper (if present)

Developer workflows
- Syncing & prefetching: `catalog.services` contains helpers like `get_or_sync_person`, `get_or_sync_company`, and `prefetch_company_filmography` to refresh cached payloads. Use these in the shell for debugging.
- Running demo commands: run the demo management commands with `--username <user>` and either `--person <tmdb_id>` or `--company <tmdb_id>` to simulate arrival/update events.
- Tests: the app includes unit tests under `accounts/tests.py` and `catalog/tests.py` — run them with `python manage.py test`.

Routes / important pages
- `/` — Home (shows followed entities grouped by role)
- `/search/` — Search (DB-first, TMDb top-up)
- `/collaboration/` — Collaboration Finder (shared movies between people)
- `/upcoming/` — Upcoming movies for followed people and companies
- `/person/<tmdb_id>/`, `/company/<tmdb_id>/`, `/movie/<tmdb_id>/` — detail pages
- `/tmdb/<endpoint>/` — TMDb proxy endpoint (server-side GET forwarding)
- `/me/` — User profile

Testing and running automated checks
- `python manage.py test` runs the test suite. Fix DB credentials and local services before running.

Deployment notes
- Production should run with `DEBUG=0` and a secure `SECRET_KEY`.
- Use a suitable WSGI server (Gunicorn / Daphne for ASGI) behind a reverse proxy.
- Configure a managed MySQL and (optionally) Redis instance. Ensure `ALLOWED_HOSTS` is set appropriately.
- Media/static: collect static files to `STATIC_ROOT` and serve media from `MEDIA_ROOT` via your webserver or object storage in production.

Where to look in the codebase (quick pointers)
- Follow / domain models: [catalog/models.py](catalog/models.py)
- TMDb client and caching: [catalog/tmdb.py](catalog/tmdb.py) and [catalog/services.py](catalog/services.py)
- Management commands: [catalog/management/commands](catalog/management/commands)
- Settings / env: [config/settings.py](config/settings.py)
- Templates: [templates](templates) and static assets under [static](static)

Troubleshooting
- Django import errors: ensure virtualenv is active and packages from `requirements.txt` are installed
- MySQL connection issues: validate `DB_HOST`/`DB_USER`/`DB_PASSWORD` and make sure the database uses `utf8mb4`
- TMDb 401/403: confirm `TMDB_API_KEY` and `TMDB_API_READ_ACCESS_TOKEN` values

Contributing
- Fork, create a feature branch, and open a pull request describing your change.
- Run tests before submitting PRs. Add tests for new features where appropriate.

License & contact
- This repository does not include an explicit license file. Add one if you intend to publish or share externally.
- For questions, open an issue or contact the repository owner.

---

If you'd like, I can also:
- add a `.env.example` file to the repo with the variables above
- add short usage examples for the most common management commands
- run the test suite here (it may require DB + Redis available locally)

Updated README written and saved to the repository root.