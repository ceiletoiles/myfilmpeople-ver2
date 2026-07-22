(function () {
  function getCookie(name) {
    const cookieValue = '; ' + document.cookie;
    const parts = cookieValue.split('; ' + name + '=');
    if (parts.length !== 2) return '';
    return decodeURIComponent(parts.pop().split(';').shift() || '');
  }

  function initDiaryImportProgress() {
    const form = document.querySelector('[data-diary-import-form]');
    const progressWrap = document.querySelector('[data-diary-import-progress]');
    const textEl = document.querySelector('[data-diary-import-progress-text]');
    const currentEl = document.querySelector('[data-diary-import-progress-current]');
    const metaEl = document.querySelector('[data-diary-import-progress-meta]');
    const barRoot = document.querySelector('[data-diary-import-progress-bar]');
    const barFill = document.querySelector('[data-diary-import-progress-fill]');

    if (!form || !progressWrap || !textEl || !barRoot || !barFill) return;

    let pollTimer = null;
    let activeProgressUrl = null;
    let activeButton = null;

    function setHidden(hidden) {
      progressWrap.hidden = !!hidden;
    }

    function setText(msg) {
      textEl.textContent = msg || '';
    }

    function setMeta(msg) {
      if (!metaEl) return;
      metaEl.textContent = msg || '';
    }

    function setCurrent(msg) {
      if (!currentEl) return;
      currentEl.textContent = msg || '';
    }

    function setPercent(pct) {
      const p = Math.max(0, Math.min(100, Number(pct || 0)));
      barFill.style.width = p.toFixed(1) + '%';
      barRoot.setAttribute('aria-valuenow', String(Math.round(p)));
    }

    function clearPolling() {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function formatSummary(data) {
      const processed = Number(data.processed_rows || 0);
      const total = Number(data.total_rows || 0);
      const created = Number(data.created_entries || 0);
      const updated = Number(data.updated_entries || 0);
      const skipped = Number(data.skipped_rows || 0);
      return [
        processed + '/' + total + ' processed',
        'Imported ' + (created + updated),
        'Skipped ' + skipped
      ].join(' | ');
    }

    function applyProgress(data) {
      const processed = Number(data.processed_rows || 0);
      const total = Number(data.total_rows || 0);
      const pct = total > 0 ? (processed / total) * 100 : 100;
      setPercent(pct);
      setText((data.current_label || 'Importing...').trim());
      setCurrent((data.current_title || '').trim());
      setMeta(formatSummary(data));
    }

    async function pollOnce() {
      if (!activeProgressUrl) return;
      try {
        const resp = await fetch(activeProgressUrl, {
          method: 'GET',
          credentials: 'same-origin',
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          }
        });
        const data = await resp.json().catch(() => null);
        if (!resp.ok || !data || data.ok === false) {
          clearPolling();
          if (activeButton) activeButton.disabled = false;
          setText('Import status unavailable.');
          setCurrent('');
          return;
        }

        applyProgress(data);
        const status = String(data.status || '');
        if (status === 'done' || status === 'done_with_errors' || status === 'failed') {
          clearPolling();
          if (status === 'done' || status === 'done_with_errors') {
            setPercent(100);
          }
          if (activeButton) activeButton.disabled = false;
          setText((data.message || '').trim() || 'Import complete.');
          setCurrent((data.current_title || '').trim());
          setMeta(formatSummary(data));
          activeProgressUrl = null;
        }
      } catch (_) {
        clearPolling();
        if (activeButton) activeButton.disabled = false;
        setText('Network error while importing.');
      }
    }

    async function startImport() {
      const fileInput = form.querySelector('input[type="file"]');
      if (!(fileInput instanceof HTMLInputElement) || !fileInput.files || !fileInput.files.length) {
        setHidden(false);
        setPercent(0);
        setText('Choose a Letterboxd export file first.');
        setCurrent('');
        setMeta('');
        return;
      }

      const btn = form.querySelector('button[type="submit"], button:not([type])');
      activeButton = btn;
      if (activeButton) activeButton.disabled = true;

      clearPolling();
      setHidden(false);
      setPercent(3);
      setText('Starting import...');
      setCurrent('');
      setMeta('');

      try {
        const resp = await fetch(form.getAttribute('data-diary-import-start-url') || form.action, {
          method: 'POST',
          credentials: 'same-origin',
          body: new FormData(form),
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': getCookie('csrftoken')
          }
        });
        const data = await resp.json().catch(() => null);
        if (!resp.ok || !data || data.ok === false) {
          if (activeButton) activeButton.disabled = false;
          setText((data && (data.error || data.message)) || ('Import failed (' + resp.status + ')'));
          setCurrent('');
          setMeta('');
          activeProgressUrl = null;
          return;
        }

        activeProgressUrl = data.progress_url || null;
        if (!activeProgressUrl) {
          if (activeButton) activeButton.disabled = false;
          setText('Could not start the import progress check.');
          setCurrent('');
          setMeta('');
          return;
        }

        await pollOnce();
        pollTimer = window.setInterval(pollOnce, 900);
      } catch (_) {
        if (activeButton) activeButton.disabled = false;
        setText('Network error.');
        setCurrent('');
        setMeta('');
      }
    }

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      startImport();
    });
  }

  function initDiarySyncProgress() {
    const button = document.querySelector('[data-diary-sync-button]');
    const progressWrap = document.querySelector('[data-diary-sync-progress]');
    const textEl = document.querySelector('[data-diary-sync-progress-text]');
    const currentEl = document.querySelector('[data-diary-sync-progress-current]');
    const metaEl = document.querySelector('[data-diary-sync-progress-meta]');
    const barRoot = document.querySelector('[data-diary-sync-progress-bar]');
    const barFill = document.querySelector('[data-diary-sync-progress-fill]');

    if (!button || !progressWrap || !textEl || !barRoot || !barFill) return;

    let pollTimer = null;
    let activeProgressUrl = progressWrap.getAttribute('data-diary-sync-progress-url') || '';
    let activeButton = button;

    function setHidden(hidden) {
      progressWrap.hidden = !!hidden;
    }

    function setText(msg) {
      textEl.textContent = msg || '';
    }

    function setMeta(msg) {
      if (!metaEl) return;
      metaEl.textContent = msg || '';
    }

    function setCurrent(msg) {
      if (!currentEl) return;
      currentEl.textContent = msg || '';
    }

    function setPercent(pct) {
      const p = Math.max(0, Math.min(100, Number(pct || 0)));
      barFill.style.width = p.toFixed(1) + '%';
      barRoot.setAttribute('aria-valuenow', String(Math.round(p)));
    }

    function clearPolling() {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function formatSummary(data) {
      const processed = Number(data.processed_items || 0);
      const total = Number(data.total_items || 0);
      const created = Number(data.created_entries || 0);
      const updated = Number(data.updated_entries || 0);
      const skipped = Number(data.skipped_items || 0);
      return [
        processed + '/' + total + ' processed',
        'Imported ' + (created + updated),
        'Skipped ' + skipped
      ].join(' | ');
    }

    function applyProgress(data) {
      const processed = Number(data.processed_items || 0);
      const total = Number(data.total_items || 0);
      const pct = total > 0 ? (processed / total) * 100 : 100;
      setPercent(pct);
      setText((data.current_label || 'Syncing...').trim());
      setCurrent((data.current_title || '').trim());
      setMeta(formatSummary(data));
    }

    async function pollOnce() {
      if (!activeProgressUrl) return;
      try {
        const resp = await fetch(activeProgressUrl, {
          method: 'GET',
          credentials: 'same-origin',
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          }
        });
        const data = await resp.json().catch(() => null);
        if (!resp.ok || !data || data.ok === false) {
          clearPolling();
          if (activeButton) activeButton.disabled = false;
          setText('Sync status unavailable.');
          setCurrent('');
          return;
        }

        applyProgress(data);
        const status = String(data.status || '');
        if (status === 'done' || status === 'done_with_errors' || status === 'failed') {
          clearPolling();
          if (status === 'done' || status === 'done_with_errors') {
            setPercent(100);
          }
          if (activeButton) activeButton.disabled = false;
          setText((data.message || '').trim() || 'Sync complete.');
          setCurrent((data.current_title || '').trim());
          setMeta(formatSummary(data));
          activeProgressUrl = '';
        }
      } catch (_) {
        clearPolling();
        if (activeButton) activeButton.disabled = false;
        setText('Network error while syncing.');
      }
    }

    async function startSync() {
      if (activeButton) activeButton.disabled = true;
      clearPolling();
      setHidden(false);
      setPercent(3);
      setText('Starting sync...');
      setCurrent('');
      setMeta('');

      try {
        const resp = await fetch(button.getAttribute('data-diary-sync-start-url') || '', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': getCookie('csrftoken')
          }
        });
        const data = await resp.json().catch(() => null);
        if (!resp.ok || !data || data.ok === false) {
          if (activeButton) activeButton.disabled = false;
          setText((data && (data.error || data.message)) || ('Sync failed (' + resp.status + ')'));
          setCurrent('');
          setMeta('');
          activeProgressUrl = '';
          return;
        }

        activeProgressUrl = data.progress_url || '';
        if (!activeProgressUrl) {
          if (activeButton) activeButton.disabled = false;
          setText('Could not start sync progress polling.');
          setCurrent('');
          setMeta('');
          return;
        }

        await pollOnce();
        pollTimer = window.setInterval(pollOnce, 900);
      } catch (_) {
        if (activeButton) activeButton.disabled = false;
        setText('Network error.');
        setCurrent('');
        setMeta('');
      }
    }

    button.addEventListener('click', function (event) {
      event.preventDefault();
      startSync();
    });

    if (activeProgressUrl) {
      setHidden(false);
      setPercent(0);
      pollOnce();
      pollTimer = window.setInterval(pollOnce, 900);
    }
  }

  function initDiaryQuickMenu() {
    const toggles = document.querySelectorAll('[data-diary-panel-toggle]');
    toggles.forEach(function (button) {
      button.addEventListener('click', function () {
        const targetId = button.getAttribute('data-diary-panel-toggle');
        const target = targetId ? document.getElementById(targetId) : null;
        if (!target) return;
        target.hidden = !target.hidden;
      });
    });
  }

  function initDiaryEntryEditor() {
    const modal = document.querySelector('[data-diary-editor]');
    const form = document.querySelector('[data-diary-editor-form]');
    const titleEl = document.querySelector('[data-diary-editor-title]');
    const metaEl = document.querySelector('[data-diary-editor-meta]');
    const posterEl = document.querySelector('[data-diary-editor-poster]');
    const viewLink = document.querySelector('[data-diary-editor-view]');
    const placeholderPoster = modal ? (modal.getAttribute('data-diary-placeholder-poster') || '') : '';
    const searchUrl = modal ? (modal.getAttribute('data-diary-editor-search-url') || '') : '';
    const tmdbIdInput = form ? form.querySelector('input[name="tmdb_id"]') : null;
    const ratingInput = form ? form.querySelector('input[name="rating"]') : null;
    const likedInput = form ? form.querySelector('input[name="liked"]') : null;
    const rewatchInput = form ? form.querySelector('input[name="rewatch"]') : null;
    const reviewInput = form ? form.querySelector('textarea[name="review"]') : null;
    const returnToInput = form ? form.querySelector('input[name="return_to"]') : null;
    const searchInput = modal ? modal.querySelector('[data-diary-editor-search-input]') : null;
    const searchButton = modal ? modal.querySelector('[data-diary-editor-search-button]') : null;
    const searchResults = modal ? modal.querySelector('[data-diary-editor-search-results]') : null;
    const searchError = modal ? modal.querySelector('[data-diary-editor-search-error]') : null;
    const cards = document.querySelectorAll('[data-diary-entry-card]');

    if (
      !modal ||
      !form ||
      !titleEl ||
      !metaEl ||
      !posterEl ||
      !viewLink ||
      !tmdbIdInput ||
      !searchInput ||
      !searchButton ||
      !searchResults ||
      !searchError
    ) {
      return;
    }

    let activeCard = null;
    let longPressTimer = null;
    let longPressTriggered = false;
    let cardClickTimer = null;
    let searchToken = 0;
    let searchDebounceTimer = null;
    const scrollStorageKey = 'diary-scroll:' + window.location.pathname;

    function saveScrollPosition() {
      try {
        window.sessionStorage.setItem(scrollStorageKey, String(window.scrollY || window.pageYOffset || 0));
      } catch (_) {}
    }

    function restoreScrollPosition() {
      let stored = null;
      try {
        stored = window.sessionStorage.getItem(scrollStorageKey);
      } catch (_) {
        stored = null;
      }
      if (!stored) return;
      const y = Math.max(0, Number(stored || 0));
      try {
        window.sessionStorage.removeItem(scrollStorageKey);
      } catch (_) {}
      window.requestAnimationFrame(function () {
        window.scrollTo(0, y);
      });
    }

    function setHidden(hidden) {
      modal.hidden = !!hidden;
      document.body.classList.toggle('modal-open', !hidden);
    }

    function setSearchError(message) {
      if (!message) {
        searchError.hidden = true;
        searchError.textContent = '';
        return;
      }
      searchError.hidden = false;
      searchError.textContent = message;
    }

    function clearSearchResults() {
      searchResults.innerHTML = '';
      searchResults.hidden = true;
    }

    function posterUrl(path) {
      return path ? ('https://image.tmdb.org/t/p/w154' + path) : placeholderPoster;
    }

    function buildStars(rating) {
      const value = Number(rating || 0);
      const fullStars = Math.max(0, Math.min(5, Math.floor(value)));
      const hasHalf = value - fullStars >= 0.5;
      const result = document.createElement('span');
      result.className = 'diary-rating-stars';
      for (let idx = 0; idx < 5; idx += 1) {
        const star = document.createElement('span');
        star.className = 'diary-star' + (idx < fullStars ? ' is-filled' : (idx === fullStars && hasHalf ? ' is-half' : ' is-empty'));
        star.setAttribute('aria-hidden', 'true');
        star.textContent = idx < fullStars ? '★' : '☆';
        result.appendChild(star);
      }
      return result;
    }

    function updateEntryCardFromResponse(data) {
      if (!activeCard || !data || !data.entry) return;
      const entry = data.entry;
      activeCard.setAttribute('data-entry-rating', entry.rating || '');
      activeCard.setAttribute('data-entry-liked', entry.liked ? '1' : '0');
      activeCard.setAttribute('data-entry-rewatch', entry.rewatch ? '1' : '0');
      activeCard.setAttribute('data-entry-review', entry.review || '');
      activeCard.setAttribute('data-entry-poster-path', entry.poster_path || '');
      activeCard.setAttribute('data-entry-movie-url', entry.tmdb_id ? ('/movie/' + String(entry.tmdb_id) + '/') : '');

      const titleEl = activeCard.querySelector('.diary-entry-title');
      if (titleEl && entry.official_title) {
        titleEl.textContent = titleEl.textContent || entry.official_title;
      }

      const posterEl = activeCard.querySelector('.diary-entry-poster');
      if (posterEl) {
        posterEl.src = entry.poster_path ? ('https://image.tmdb.org/t/p/w342' + entry.poster_path) : placeholderPoster;
      }

      const linkEl = activeCard.parentElement ? activeCard.parentElement.querySelector('.diary-entry-link') : null;
      if (linkEl) {
        if (entry.tmdb_id) {
          linkEl.href = '/movie/' + String(entry.tmdb_id) + '/';
          linkEl.hidden = false;
        } else {
          linkEl.hidden = true;
        }
      }

      const badgesEl = activeCard.querySelector('.diary-badges');
      if (badgesEl) {
        badgesEl.innerHTML = '';
        if (entry.rating) {
          const badge = document.createElement('span');
          badge.className = 'diary-badge diary-rating-badge';
          badge.appendChild(buildStars(entry.rating));
          badgesEl.appendChild(badge);
        }
        if (entry.liked) {
          const likedBadge = document.createElement('span');
          likedBadge.className = 'diary-badge diary-icon-badge diary-icon-like';
          likedBadge.title = 'Liked';
          likedBadge.setAttribute('aria-label', 'Liked');
          const heart = document.createElement('span');
          heart.className = 'diary-icon-mark';
          heart.setAttribute('aria-hidden', 'true');
          heart.textContent = '♥';
          likedBadge.appendChild(heart);
          badgesEl.appendChild(likedBadge);
        }
        if (entry.rewatch) {
          const rewatchBadge = document.createElement('span');
          rewatchBadge.className = 'diary-badge diary-icon-badge diary-icon-rewatch';
          rewatchBadge.title = 'Rewatch';
          rewatchBadge.setAttribute('aria-label', 'Rewatch');
          rewatchBadge.textContent = '↻';
          badgesEl.appendChild(rewatchBadge);
        }
      }

      const reviewEl = activeCard.parentElement ? activeCard.parentElement.querySelector('.diary-list-review') : null;
      if (reviewEl) {
        if (entry.review) {
          reviewEl.textContent = entry.review;
          reviewEl.hidden = false;
        } else {
          reviewEl.textContent = '';
          reviewEl.hidden = true;
        }
      }
    }

    function closeEditor() {
      activeCard = null;
      searchInput.value = '';
      clearSearchResults();
      setSearchError('');
      setHidden(true);
    }

    function navigateToMovie(card) {
      if (!(card instanceof HTMLElement)) return;
      const movieUrl = card.getAttribute('data-entry-movie-url') || '';
      if (!movieUrl) {
        openEditor(card);
        return;
      }
      window.location.href = movieUrl;
    }

    function setSelectedMovie(movie) {
      if (!movie) return;
      tmdbIdInput.value = String(movie.tmdb_id || '');
      if (movie.url) {
        viewLink.href = movie.url;
        viewLink.hidden = false;
      }
      if (movie.poster_path) {
        posterEl.src = 'https://image.tmdb.org/t/p/w342' + movie.poster_path;
      }
      const selectedLabel = movie.title || 'movie';
      setSearchError('Selected ' + selectedLabel + '. Save changes to replace the stored match.');
    }

    function renderSearchResults(results) {
      searchResults.innerHTML = '';
      if (!results || !results.length) {
        clearSearchResults();
        setSearchError('No TMDb results found for that search.');
        return;
      }

      results.forEach(function (movie) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'diary-editor-search-result';

        const poster = document.createElement('img');
        poster.className = 'diary-editor-search-poster';
        poster.src = posterUrl(movie.poster_path || '');
        poster.alt = '';
        poster.loading = 'lazy';
        button.appendChild(poster);

        const body = document.createElement('span');
        body.className = 'diary-editor-search-body';

        const title = movie.title || 'Untitled';
        const releaseDate = movie.release_date || '';
        const releaseYear = releaseDate ? releaseDate.slice(0, 4) : '';
        const titleLine = document.createElement('strong');
        titleLine.textContent = title;
        body.appendChild(titleLine);

        if (releaseYear) {
          const yearLine = document.createElement('span');
          yearLine.className = 'muted';
          yearLine.textContent = releaseYear;
          body.appendChild(yearLine);
        }

        button.appendChild(body);

        button.addEventListener('click', function () {
          setSelectedMovie(movie);
          searchInput.value = movie.title + (releaseYear ? ' ' + releaseYear : '');
        });

        searchResults.appendChild(button);
      });

      searchResults.hidden = false;
      setSearchError('');
    }

    function queueSearch() {
      if (searchDebounceTimer) {
        window.clearTimeout(searchDebounceTimer);
      }
      searchDebounceTimer = window.setTimeout(function () {
        searchDebounceTimer = null;
        searchMovies();
      }, 300);
    }

    async function searchMovies() {
      const raw = (searchInput.value || '').trim();
      if (!raw) {
        setSearchError('Enter a movie title first.');
        return;
      }

      const yearMatch = raw.match(/\b(19|20)\d{2}\b/);
      const year = yearMatch ? yearMatch[0] : '';
      const token = ++searchToken;
      searchButton.disabled = true;
      clearSearchResults();
      searchResults.hidden = false;
      setSearchError('Searching TMDb...');

      try {
        const url = new URL(searchUrl || window.location.href, window.location.origin);
        url.searchParams.set('q', raw);
        if (year) {
          url.searchParams.set('year', year);
        }
        const resp = await fetch(url.toString(), {
          method: 'GET',
          credentials: 'same-origin',
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          }
        });
        const data = await resp.json().catch(() => null);
        if (token !== searchToken) return;
        if (!resp.ok || !data || data.ok === false) {
          setSearchError((data && (data.error || data.message)) || ('Search failed (' + resp.status + ')'));
          return;
        }
        renderSearchResults(data.results || []);
      } catch (_) {
        if (token === searchToken) {
          setSearchError('Network error while searching TMDb.');
        }
      } finally {
        if (token === searchToken) {
          searchButton.disabled = false;
        }
      }
    }

    function openEditor(card) {
      if (!(card instanceof HTMLElement)) return;
      activeCard = card;
      form.setAttribute('action', card.getAttribute('data-entry-update-url') || '');
      titleEl.textContent = card.getAttribute('data-entry-title') || 'Diary entry';
      metaEl.textContent = card.getAttribute('data-entry-date') || '';
      tmdbIdInput.value = '';
      if (returnToInput) {
        returnToInput.value = window.location.pathname + window.location.search + window.location.hash;
      }

      const posterPath = card.getAttribute('data-entry-poster-path') || '';
      posterEl.src = posterPath ? ('https://image.tmdb.org/t/p/w342' + posterPath) : placeholderPoster;
      posterEl.alt = '';

      if (ratingInput) {
        ratingInput.value = card.getAttribute('data-entry-rating') || '';
      }
      if (likedInput) {
        likedInput.checked = (card.getAttribute('data-entry-liked') || '') === '1';
      }
      if (rewatchInput) {
        rewatchInput.checked = (card.getAttribute('data-entry-rewatch') || '') === '1';
      }
      if (reviewInput) {
        reviewInput.value = card.getAttribute('data-entry-review') || '';
      }

      const title = card.getAttribute('data-entry-title') || '';
      const year = card.getAttribute('data-entry-year') || '';
      searchInput.value = year ? (title + ' ' + year) : title;
      clearSearchResults();
      setSearchError('');

      const movieUrl = card.getAttribute('data-entry-movie-url') || '';
      if (movieUrl) {
        viewLink.href = movieUrl;
        viewLink.hidden = false;
      } else {
        viewLink.hidden = true;
      }

      setHidden(false);
      searchInput.focus();
      searchInput.select();
    }

    function startLongPress(card) {
      clearTimeout(longPressTimer);
      longPressTriggered = false;
      longPressTimer = window.setTimeout(function () {
        longPressTriggered = true;
        openEditor(card);
      }, 520);
    }

    function cancelLongPress() {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }

    cards.forEach(function (card) {
      const isCalendarCard = card.classList.contains('diary-calendar-entry-button');

      card.addEventListener('dblclick', function (event) {
        if (cardClickTimer) {
          window.clearTimeout(cardClickTimer);
          cardClickTimer = null;
        }
        longPressTriggered = false;
        event.preventDefault();
        openEditor(card);
      });
      card.addEventListener('pointerdown', function (event) {
        if (event.pointerType === 'touch') {
          startLongPress(card);
        }
      });
      card.addEventListener('pointerup', cancelLongPress);
      card.addEventListener('pointercancel', cancelLongPress);
      card.addEventListener('pointerleave', cancelLongPress);
      if (isCalendarCard) {
        card.addEventListener('click', function (event) {
          event.preventDefault();
          if (longPressTriggered) {
            longPressTriggered = false;
            return;
          }
          if (cardClickTimer) {
            window.clearTimeout(cardClickTimer);
          }
          cardClickTimer = window.setTimeout(function () {
            cardClickTimer = null;
            navigateToMovie(card);
          }, 180);
        });
      }
      card.addEventListener('keydown', function (event) {
        if (isCalendarCard && event.key === 'Enter') {
          event.preventDefault();
          navigateToMovie(card);
          return;
        }
        if (event.key === ' ') {
          event.preventDefault();
          openEditor(card);
          return;
        }
        if (!isCalendarCard && event.key === 'Enter') {
          event.preventDefault();
          openEditor(card);
        }
      });
    });

    restoreScrollPosition();

    searchButton.addEventListener('click', function () {
      searchMovies();
    });

      searchInput.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        searchMovies();
      }
    });

    searchInput.addEventListener('input', function () {
      const value = (searchInput.value || '').trim();
      if (value.length < 3) {
        if (searchDebounceTimer) {
          window.clearTimeout(searchDebounceTimer);
          searchDebounceTimer = null;
        }
        clearSearchResults();
        setSearchError('');
        return;
      }
      queueSearch();
    });

    modal.addEventListener('click', function (event) {
      const target = event.target;
      if (target instanceof HTMLElement && target.hasAttribute('data-diary-editor-close')) {
        closeEditor();
      }
    });

    window.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !modal.hidden) {
        closeEditor();
      }
    });

    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      saveScrollPosition();
      if (activeCard) {
        cancelLongPress();
      }

      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) {
        submitButton.disabled = true;
      }

      try {
        const resp = await fetch(form.getAttribute('action') || window.location.href, {
          method: 'POST',
          credentials: 'same-origin',
          body: new FormData(form),
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRFToken': getCookie('csrftoken')
          }
        });
        const data = await resp.json().catch(() => null);
        if (!resp.ok || !data || data.ok === false) {
          setSearchError((data && (data.error || data.message)) || ('Save failed (' + resp.status + ')'));
          return;
        }

        updateEntryCardFromResponse(data);
        closeEditor();
      } catch (_) {
        setSearchError('Network error while saving.');
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
        }
      }
    });
  }

  initDiaryImportProgress();
  initDiarySyncProgress();
  initDiaryQuickMenu();
  initDiaryEntryEditor();
}());
