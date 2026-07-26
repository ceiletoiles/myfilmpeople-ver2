(function () {
  function closest(el, sel) {
    if (!el) return null;
    return el.closest(sel);
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function parseJsonScript(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || '{}');
    } catch (_) {
      return null;
    }
  }

  function parseDate(value) {
    var text = String(value || '').trim();
    if (!text) return null;
    var dt = new Date(text + 'T00:00:00');
    if (isNaN(dt.getTime())) return null;
    return dt;
  }

  function normalizeMovie(movie) {
    var id = Number(movie && movie.id ? movie.id : 0);
    var title = String((movie && (movie.title || movie.name)) || id || '-').trim();
    var posterPath = String((movie && movie.poster_path) || '').trim();
    var releaseDate = String((movie && movie.release_date) || '').trim();
    var year = String((movie && movie.year) || '').trim();
    if (!year && releaseDate.length >= 4) year = releaseDate.slice(0, 4);
    return {
      id: id,
      title: title || String(id || '-'),
      poster_path: posterPath,
      release_date: releaseDate,
      release_dt: parseDate(releaseDate),
      year: year,
    };
  }

  function sortMovies(items, tab) {
    return items.slice().sort(function (a, b) {
      var aDt = a.release_dt ? a.release_dt.getTime() : 0;
      var bDt = b.release_dt ? b.release_dt.getTime() : 0;
      var aTitle = String(a.title || '').toLowerCase();
      var bTitle = String(b.title || '').toLowerCase();

      if (tab === 'upcoming') {
        if (!a.release_dt && !b.release_dt) return aTitle.localeCompare(bTitle);
        if (!a.release_dt) return 1;
        if (!b.release_dt) return -1;
        if (aDt !== bDt) return aDt - bDt;
        return aTitle.localeCompare(bTitle);
      }

      if (!a.release_dt && !b.release_dt) return aTitle.localeCompare(bTitle);
      if (!a.release_dt) return 1;
      if (!b.release_dt) return -1;
      if (aDt !== bDt) return bDt - aDt;
      return aTitle.localeCompare(bTitle);
    });
  }

  function buildMovieCard(movie, placeholderPoster) {
    var posterHtml;
    if (movie.poster_path) {
      posterHtml =
        '<img class="filmography-poster" src="https://image.tmdb.org/t/p/w342' +
        escapeHtml(movie.poster_path) +
        '" alt="" loading="lazy" decoding="async" />';
    } else {
      posterHtml =
        '<img class="filmography-poster" src="' +
        escapeHtml(placeholderPoster) +
        '" alt="" loading="lazy" decoding="async" />';
    }

    var year = movie.year || (movie.release_date ? movie.release_date.slice(0, 4) : 'TBA');

    return (
      '<div class="filmography-item" data-filmography-item data-movie-id="' +
      escapeHtml(movie.id) +
      '">' +
      '<a class="filmography-poster-link" href="/movie/' +
      escapeHtml(movie.id) +
      '/">' +
      posterHtml +
      '</a>' +
      '<div class="filmography-meta">' +
      '<a class="filmography-title" href="/movie/' +
      escapeHtml(movie.id) +
      '/"><strong>' +
      escapeHtml(movie.title || '-') +
      '</strong></a>' +
      '<div class="filmography-year muted">' +
      escapeHtml(year || 'TBA') +
      '</div>' +
      '</div>' +
      '</div>'
    );
  }

  function buildSkeletonCards(count) {
    var html = '';
    for (var i = 0; i < count; i += 1) {
      html +=
        '<div class="filmography-item filmography-item--skeleton" aria-hidden="true">' +
        '<div class="filmography-poster filmography-poster--skeleton"></div>' +
        '<div class="filmography-meta filmography-meta--skeleton">' +
        '<div class="filmography-line filmography-line--title"></div>' +
        '<div class="filmography-line filmography-line--year"></div>' +
        '</div>' +
        '</div>';
    }
    return html;
  }

  function initCompanyFilmography() {
    var shell = document.querySelector('[data-filmography-shell]');
    var state = parseJsonScript('company-filmography-state');
    if (!shell || !state) return null;

    var grid = shell.querySelector('[data-filmography-grid]');
    var statusEl = shell.querySelector('[data-filmography-status]');
    var footerEl = shell.querySelector('[data-filmography-footer]');
    var retryBtn = shell.querySelector('[data-filmography-retry]');
    var sentinel = shell.querySelector('[data-filmography-sentinel]');
    var tabButtons = document.querySelectorAll('[data-filmography-tab]');
    var pageUrl = shell.getAttribute('data-page-url') || '';
    var placeholderPoster = shell.getAttribute('data-placeholder-poster') || '';
    var companyId = shell.getAttribute('data-company-id') || '';
    var storageKey = 'company-filmography-tab:' + companyId;
    var today = new Date();
    today.setHours(0, 0, 0, 0);

    var sourceState = {
      filmography: {
        pages: {},
        nextPage: 1,
        loading: false,
        error: '',
        hasMore: true,
        totalPages: null,
        requestedPages: {},
      },
      tba: {
        pages: {},
        nextPage: 1,
        loading: false,
        error: '',
        hasMore: true,
        totalPages: null,
        requestedPages: {},
      },
    };

    var tabScroll = {
      released: 0,
      upcoming: 0,
      tba: 0,
    };

    var activeTab = String(state.active_tab || shell.getAttribute('data-active-tab') || 'released').toLowerCase();
    if (!/^(released|upcoming|tba)$/.test(activeTab)) {
      activeTab = 'released';
    }

    var savedTab = '';
    try {
      savedTab = sessionStorage.getItem(storageKey) || '';
    } catch (_) {
      savedTab = '';
    }
    if (/^(released|upcoming|tba)$/.test(savedTab)) {
      activeTab = savedTab;
    }

    function getSourceKey(tab) {
      return tab === 'tba' ? 'tba' : 'filmography';
    }

    function getSource(tab) {
      return sourceState[getSourceKey(tab)];
    }

    function getLoadedSourceItems(sourceKey) {
      var source = sourceState[sourceKey];
      var pageNumbers = Object.keys(source.pages)
        .map(function (n) {
          return Number(n);
        })
        .filter(function (n) {
          return Number.isFinite(n) && n > 0;
        })
        .sort(function (a, b) {
          return a - b;
        });

      var seen = {};
      var items = [];
      pageNumbers.forEach(function (pageNum) {
        var page = source.pages[String(pageNum)];
        if (!page || !Array.isArray(page.items)) return;
        page.items.forEach(function (movie) {
          if (!movie || !movie.id || seen[movie.id]) return;
          seen[movie.id] = true;
          items.push(movie);
        });
      });
      return items;
    }

    function getDerivedItems(tab) {
      var sourceKey = getSourceKey(tab);
      var sourceItems = getLoadedSourceItems(sourceKey).map(normalizeMovie);
      if (tab === 'tba') {
        return sourceItems.filter(function (movie) {
          return !movie.release_dt;
        });
      }

      return sortMovies(
        sourceItems.filter(function (movie) {
          if (!movie.release_dt) return false;
          if (tab === 'released') {
            return movie.release_dt.getTime() <= today.getTime();
          }
          return movie.release_dt.getTime() > today.getTime();
        }),
        tab
      );
    }

    function setActiveButtons(tab) {
      tabButtons.forEach(function (button) {
        var isActive = button.getAttribute('data-filmography-tab') === tab;
        button.classList.toggle('active', isActive);
        button.setAttribute('aria-selected', isActive ? 'true' : 'false');
      });
    }

    function setStatus(text) {
      if (!statusEl) return;
      statusEl.innerHTML = text || '';
    }

    function setFooter(text) {
      if (!footerEl) return;
      footerEl.textContent = text || '';
    }

    function renderSkeleton(tab) {
      if (!grid) return;
      grid.innerHTML = buildSkeletonCards(tab === 'tba' ? 12 : 20);
    }

    function renderItems(tab, items) {
      if (!grid) return;
      if (!items.length) {
        grid.innerHTML = '<p class="muted filmography-empty" data-filmography-empty>No movies found for this tab.</p>';
        return;
      }
      grid.innerHTML = items
        .map(function (movie) {
          return buildMovieCard(movie, placeholderPoster);
        })
        .join('');
    }

    function updateChrome(tab, items) {
      var source = getSource(tab);
      if (retryBtn) retryBtn.hidden = !source.error;

      if (source.error) {
        setStatus(
          '<div class="filmography-error">Failed to load movies. <button type="button" class="link-button" data-filmography-retry-inline>Retry</button></div>'
        );
      } else if (source.loading) {
        setStatus('<span class="muted">Loading movies...</span>');
      } else {
        setStatus('');
      }

      if (!items.length && !source.loading && !source.error && source.hasMore) {
        setFooter('Loading more...');
      } else if (!items.length && !source.loading && !source.error) {
        setFooter('No movies found for this tab.');
      } else if (source.hasMore) {
        setFooter('More movies will load as you scroll.');
      } else {
        setFooter('No more movies.');
      }
    }

    function persistTab(tab) {
      try {
        sessionStorage.setItem(storageKey, tab);
      } catch (_) {
        // ignore
      }

      var url = new URL(window.location.href);
      url.searchParams.set('tab', tab);
      window.history.replaceState({}, '', url.toString());
    }

    function renderTab(tab, opts) {
      var options = opts || {};
      var preserveScroll = options.preserveScroll !== false;
      var previousScroll = tabScroll[tab] || 0;
      var source = getSource(tab);
      var items = getDerivedItems(tab);

      activeTab = tab;
      setActiveButtons(tab);
      persistTab(tab);

      if (!options.skipRender) {
        if (source.loading && !items.length) {
          renderSkeleton(tab);
        } else {
          renderItems(tab, items);
        }
        updateChrome(tab, items);
      }

      if (preserveScroll) {
        window.requestAnimationFrame(function () {
          window.scrollTo(0, previousScroll);
        });
      }

      if (sentinel && window.IntersectionObserver && source.hasMore) {
        observeSentinel();
      }

      if (!source.loading && !source.error && !items.length && source.hasMore) {
        loadMore(tab);
      }
    }

    function applyPage(sourceKey, pageNum, payload) {
      var source = sourceState[sourceKey];
      var items = Array.isArray(payload.items) ? payload.items : [];
      source.pages[String(pageNum)] = {
        page: pageNum,
        items: items.map(normalizeMovie),
      };
      source.nextPage = Math.max(source.nextPage, pageNum + 1);
      source.hasMore = Boolean(payload.has_more);
      source.totalPages = payload.total_pages || null;
      source.error = '';
      source.requestedPages[String(pageNum)] = true;
    }

    function fetchPage(tab, pageNum) {
      var url = new URL(pageUrl, window.location.origin);
      url.searchParams.set('tab', tab);
      url.searchParams.set('page', String(pageNum));
      return fetch(url.toString(), { credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (resp) {
        if (!resp.ok) {
          throw new Error('HTTP ' + resp.status);
        }
        return resp.json();
      });
    }

    function loadMore(tab) {
      tab = tab || activeTab;
      var sourceKey = getSourceKey(tab);
      var source = sourceState[sourceKey];
      var pageNum = source.nextPage || 1;
      if (source.loading || source.requestedPages[String(pageNum)] || !source.hasMore) return Promise.resolve();

      source.loading = true;
      source.error = '';
      if (!getDerivedItems(tab).length) {
        renderSkeleton(tab);
      } else {
        updateChrome(tab, getDerivedItems(tab));
      }

      return fetchPage(tab, pageNum)
        .then(function (payload) {
          applyPage(sourceKey, Number(payload.page || pageNum), payload);
          source.loading = false;
          source.error = '';
          renderTab(activeTab, { preserveScroll: true, skipRender: false });
        })
        .catch(function (err) {
          source.loading = false;
          source.error = err && err.message ? err.message : 'Request failed';
          source.requestedPages[String(pageNum)] = false;
          if (activeTab === tab) {
            updateChrome(tab, getDerivedItems(tab));
            renderItems(tab, getDerivedItems(tab));
          }
        });
    }

    function observeSentinel() {
      if (!window.IntersectionObserver || !sentinel) return;
      if (observeSentinel.observer) {
        observeSentinel.observer.disconnect();
      }

      observeSentinel.observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            var source = getSource(activeTab);
            if (!source.loading && source.hasMore) {
              loadMore(activeTab);
            }
          });
        },
        { rootMargin: '0px 0px 1200px 0px', threshold: 0 }
      );

      observeSentinel.observer.observe(sentinel);
    }

    function initInitialState() {
      var sourceKey = state.source === 'tba' ? 'tba' : 'filmography';
      var initialPage = Number(state.page || 1);
      var sourceItems = Array.isArray(state.source_items) ? state.source_items : [];
      sourceState[sourceKey].pages[String(initialPage)] = {
        page: initialPage,
        items: sourceItems.map(normalizeMovie),
      };
      sourceState[sourceKey].nextPage = initialPage + 1;
      sourceState[sourceKey].hasMore = Boolean(state.has_more);
      sourceState[sourceKey].totalPages = state.total_pages || null;
      sourceState[sourceKey].requestedPages[String(initialPage)] = true;
      if (activeTab === 'tba') {
        sourceState.tba.pages[String(initialPage)] = sourceState[sourceKey].pages[String(initialPage)];
      } else {
        sourceState.filmography.pages[String(initialPage)] = sourceState[sourceKey].pages[String(initialPage)];
      }
    }

    function wireRetryButtons() {
      if (retryBtn) {
        retryBtn.addEventListener('click', function () {
          loadMore(activeTab);
        });
      }

      var inlineRetry = shell.querySelector('[data-filmography-retry-inline]');
      if (inlineRetry) {
        inlineRetry.addEventListener('click', function () {
          loadMore(activeTab);
        });
      }
    }

    tabButtons.forEach(function (button) {
      button.addEventListener('click', function () {
        var tab = button.getAttribute('data-filmography-tab') || 'released';
        if (tab === activeTab) return;
        tabScroll[activeTab] = window.scrollY || 0;
        activeTab = tab;
        renderTab(tab, { preserveScroll: true, skipRender: false });
        wireRetryButtons();
      });
    });

    initInitialState();
    setActiveButtons(activeTab);
    renderTab(activeTab, { preserveScroll: false, skipRender: false });
    wireRetryButtons();

    if (window.IntersectionObserver) {
      observeSentinel();
    }

    window.addEventListener('scroll', function () {
      tabScroll[activeTab] = window.scrollY || 0;
    }, { passive: true });

    return {
      loadMore: loadMore,
      renderTab: renderTab,
    };
  }

  var filmographyApp = initCompanyFilmography();

  function closeAllKebabMenus(exceptDetails) {
    document.querySelectorAll('details[data-kebab-menu][open]').forEach(function (d) {
      if (exceptDetails && d === exceptDetails) return;
      d.removeAttribute('open');
    });
  }

  document.addEventListener('click', function (e) {
    var target = e.target;

    var openBtn = closest(target, '[data-open-dialog]');
    if (openBtn) {
      var selector = openBtn.getAttribute('data-open-dialog');
      if (!selector) return;
      var dialog = document.querySelector(selector);
      if (dialog && typeof dialog.showModal === 'function') {
        try {
          dialog.showModal();
        } catch (_) {
          // ignore
        }
      }
      var menu = closest(openBtn, 'details[data-kebab-menu]');
      if (menu) menu.removeAttribute('open');
      return;
    }

    var retryInline = closest(target, '[data-filmography-retry-inline]');
    if (retryInline && filmographyApp) {
      filmographyApp.loadMore();
      return;
    }

    var clickedMenu = closest(target, 'details[data-kebab-menu]');
    if (!clickedMenu) {
      closeAllKebabMenus(null);
    }
  });

  document.addEventListener('toggle', function (e) {
    var details = e.target;
    if (!(details instanceof HTMLDetailsElement)) return;
    if (!details.matches('details[data-kebab-menu]')) return;
    if (details.open) closeAllKebabMenus(details);
  });
})();
