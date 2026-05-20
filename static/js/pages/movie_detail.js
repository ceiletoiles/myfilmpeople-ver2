(function () {
  var tabButtons = document.querySelectorAll('.movie-tab-btn');
  var tabPanels = document.querySelectorAll('.tab-panel');

  if (!tabButtons.length || !tabPanels.length) {
    return;
  }

  function setActiveTab(tabName, updateUrl) {
    tabButtons.forEach(function (button) {
      button.classList.toggle('active', button.dataset.tab === tabName);
    });

    tabPanels.forEach(function (panel) {
      panel.classList.toggle('active', panel.dataset.panel === tabName);
    });

    if (updateUrl) {
      var url = new URL(window.location.href);
      url.searchParams.set('tab', tabName);
      window.history.replaceState({}, '', url.toString());
    }
  }

  tabButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      var tabName = button.dataset.tab;
      setActiveTab(tabName, true);
    });
  });

  var urlTab = new URLSearchParams(window.location.search).get('tab');
  if (urlTab && document.querySelector('.tab-panel[data-panel="' + urlTab + '"]')) {
    setActiveTab(urlTab, false);
    return;
  }

  var activeBtn = document.querySelector('.movie-tab-btn.active');
  setActiveTab(activeBtn ? activeBtn.dataset.tab : 'cast', false);
})();

(function () {
  var sections = document.querySelectorAll('[data-lazy-movies]');
  if (!sections.length) {
    return;
  }

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function buildCard(movie, placeholderUrl) {
    var title = escapeHtml(movie.title || '-');
    var year = escapeHtml(movie.year || 'TBA');
    var href = '/movie/' + String(movie.id) + '/';

    var poster = '';
    if (movie.poster_path) {
      poster =
        '<img class="movie-card-poster" src="https://image.tmdb.org/t/p/w300' +
        escapeHtml(movie.poster_path) +
        '" alt="" loading="lazy" onerror="this.onerror=null;this.src=\'' +
        escapeHtml(placeholderUrl) +
        '\';" />';
    } else {
      poster =
        '<img class="movie-card-poster" src="' +
        escapeHtml(placeholderUrl) +
        '" alt="" loading="lazy" />';
    }

    return (
      '<a class="movie-card" role="listitem" href="' +
      href +
      '">' +
      poster +
      '<div class="movie-card-info">' +
      '<div class="movie-card-title">' +
      title +
      '</div>' +
      '<div class="movie-card-year">' +
      year +
      '</div>' +
      '</div>' +
      '</a>'
    );
  }

  function loadSection(section) {
    if (section.dataset.loaded === '1') {
      return;
    }
    section.dataset.loaded = '1';

    var url = section.getAttribute('data-src');
    var emptyText = section.getAttribute('data-empty') || 'No movies available.';
    var placeholderUrl = section.getAttribute('data-placeholder') || '';

    var statusEl = section.querySelector('[data-movies-status]');
    var gridEl = section.querySelector('[data-movies-grid]');
    if (!url || !gridEl) {
      return;
    }

    fetch(url, { credentials: 'same-origin' })
      .then(function (resp) {
        if (!resp.ok) {
          throw new Error('HTTP ' + resp.status);
        }
        return resp.json();
      })
      .then(function (data) {
        var movies = data && Array.isArray(data.movies) ? data.movies : [];
        if (!movies.length) {
          section.style.display = 'none';
          return;
        }

        gridEl.innerHTML = movies
          .map(function (m) {
            return buildCard(m, placeholderUrl);
          })
          .join('');
        if (statusEl) {
          statusEl.style.display = 'none';
        }
      })
      .catch(function () {
        if (statusEl) {
          statusEl.textContent = emptyText;
        }
      });
  }

  if (!('IntersectionObserver' in window)) {
    sections.forEach(function (s) {
      loadSection(s);
    });
    return;
  }

  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          loadSection(entry.target);
          observer.unobserve(entry.target);
        }
      });
    },
    { rootMargin: '200px 0px' }
  );

  sections.forEach(function (section) {
    observer.observe(section);
  });
})();

(function () {
  // Desktop-only: auto-scroll past the full-screen backdrop into the content.
  // CSS alone can't initiate scrolling; keep this minimal and respectful.

  var isDesktop =
    window.matchMedia &&
    window.matchMedia('(min-width: 901px) and (hover: hover) and (pointer: fine)').matches;

  if (!isDesktop) {
    return;
  }

  // Don't override browser-restored scroll positions (back/forward) or deep links.
  if (window.location.hash) {
    return;
  }
  if (window.scrollY && window.scrollY > 0) {
    return;
  }

  var backdrop = document.querySelector('.movie-backdrop.movie-backdrop-visible');
  var container = document.querySelector('.movie-page-container');
  if (!backdrop || !container) {
    return;
  }

  var prefersReducedMotion =
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function scrollToContent() {
    var rect = container.getBoundingClientRect();
    var targetY = rect.top + (window.pageYOffset || 0);

    // If the backdrop is tiny (or already at content), don't animate.
    if (!targetY || targetY < 40) {
      return;
    }

    window.scrollTo({
      top: targetY,
      behavior: prefersReducedMotion ? 'auto' : 'smooth',
    });
  }

  // Let the first paint happen so the user sees the hero briefly.
  window.requestAnimationFrame(function () {
    window.setTimeout(scrollToContent, 140);
  });
})();

(function () {
  // Keep the title+meta column aligned with the poster height.
  // We shrink the title font-size (within a reasonable range) until the
  // `.movie-details` content fits without overflowing.

  var title = document.querySelector('.movie-title');
  var details = document.querySelector('.movie-details');
  var poster = document.querySelector('.movie-poster-image');

  if (!title || !details || !poster) {
    return;
  }

  var scheduled = false;

  function isMobile() {
    return (
      window.matchMedia &&
      window.matchMedia('(max-width: 700px)').matches
    );
  }

  function fitTitleToPoster() {
    if (scheduled) {
      return;
    }
    scheduled = true;

    window.requestAnimationFrame(function () {
      scheduled = false;

      var posterRect = poster.getBoundingClientRect();
      var posterHeight = Math.round(posterRect.height || 0);
      if (!posterHeight) {
        return;
      }

      // Match the details column height to the poster height.
      details.style.height = String(posterHeight) + 'px';
      details.style.overflow = 'hidden';

      // Reset any previous inline styles so we start from the CSS baseline.
      title.style.fontSize = '';
      title.style.lineHeight = '';

      var computed = window.getComputedStyle(title);
      var startSize = parseFloat(computed.fontSize || '0') || (isMobile() ? 28 : 35);

      // Don't go above the intended baseline sizes.
      var maxSize = Math.min(startSize, isMobile() ? 28 : 35);
      var minSize = isMobile() ? 14 : 16;
      var size = maxSize;

      // Apply and shrink until the whole details block fits.
      title.style.fontSize = String(size) + 'px';

      var guard = 0;
      while (guard < 48 && details.scrollHeight > posterHeight + 1 && size > minSize) {
        size -= 1;
        title.style.fontSize = String(size) + 'px';
        guard += 1;
      }

      // Last resort: tighten title line-height a touch if still overflowing.
      if (details.scrollHeight > posterHeight + 1) {
        title.style.lineHeight = '1.05';
      }
    });
  }

  // Run after the poster image dimensions are known.
  if (poster.complete) {
    fitTitleToPoster();
  } else {
    poster.addEventListener('load', fitTitleToPoster, { once: true });
  }

  window.addEventListener('resize', fitTitleToPoster);
  // Fonts/layout can shift shortly after first paint.
  window.setTimeout(fitTitleToPoster, 60);
})();
