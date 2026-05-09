from __future__ import annotations

# Public re-exports to keep imports stable:
# - from catalog import views as catalog_views
# - from . import views (in catalog/urls.py)

from .collaboration import collaboration_finder, collaboration_suggest
from .company import company_detail
from .follow import (
	company_note,
	company_sync,
	company_unfollow,
	follow,
	person_note,
	person_sync,
	person_unfollow,
	sync_all_followed,
)
from .home import home
from . import misc as misc
from .movie import movie_detail, movie_related, movie_similar
from .new_arrivals import new_arrivals
from .person import person_detail, person_toggle_self_appearances
from .recent import recent
from .search import search, search_suggest
from .sync_jobs import (
	company_sync_progress,
	company_sync_start,
	person_sync_progress,
	person_sync_start,
	sync_all_followed_progress,
	sync_all_followed_start,
)
from .tmdb import tmdb_proxy
from .upcoming import upcoming

__all__ = [
	"collaboration_finder",
	"collaboration_suggest",
	"company_detail",
	"company_note",
	"company_sync_progress",
	"company_sync_start",
	"company_sync",
	"company_unfollow",
	"follow",
	"home",
	"misc",
	"movie_detail",
	"movie_related",
	"movie_similar",
	"new_arrivals",
	"person_detail",
	"person_toggle_self_appearances",
	"person_note",
	"person_sync_progress",
	"person_sync_start",
	"person_sync",
	"person_unfollow",
	"recent",
	"search",
	"search_suggest",
	"sync_all_followed",
	"sync_all_followed_progress",
	"sync_all_followed_start",
	"tmdb_proxy",
	"upcoming",
]
