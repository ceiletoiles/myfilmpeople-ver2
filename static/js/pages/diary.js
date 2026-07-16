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
      const review = Number(data.require_review || 0);
      const skipped = Number(data.skipped_rows || 0);
      return [
        processed + '/' + total + ' processed',
        'Imported ' + (created + updated),
        'Review ' + review,
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
    const ratingInput = form ? form.querySelector('input[name="rating"]') : null;
    const likedInput = form ? form.querySelector('input[name="liked"]') : null;
    const rewatchInput = form ? form.querySelector('input[name="rewatch"]') : null;
    const reviewInput = form ? form.querySelector('textarea[name="review"]') : null;
    const placeholderPoster = modal ? (modal.getAttribute('data-diary-placeholder-poster') || '') : '';
    const cards = document.querySelectorAll('[data-diary-entry-card]');

    if (!modal || !form || !titleEl || !metaEl || !posterEl || !viewLink) return;

    let activeCard = null;
    let longPressTimer = null;

    function setHidden(hidden) {
      modal.hidden = !!hidden;
      document.body.classList.toggle('modal-open', !hidden);
    }

    function closeEditor() {
      activeCard = null;
      setHidden(true);
    }

    function openEditor(card) {
      if (!(card instanceof HTMLElement)) return;
      activeCard = card;
      form.setAttribute('action', card.getAttribute('data-entry-update-url') || '');
      titleEl.textContent = card.getAttribute('data-entry-title') || 'Diary entry';
      metaEl.textContent = card.getAttribute('data-entry-date') || '';

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

      const movieUrl = card.getAttribute('data-entry-movie-url') || '';
      if (movieUrl) {
        viewLink.href = movieUrl;
        viewLink.hidden = false;
      } else {
        viewLink.hidden = true;
      }

      setHidden(false);
      if (ratingInput) {
        ratingInput.focus();
      } else if (reviewInput) {
        reviewInput.focus();
      }
    }

    function startLongPress(card) {
      clearTimeout(longPressTimer);
      longPressTimer = window.setTimeout(function () {
        openEditor(card);
      }, 520);
    }

    function cancelLongPress() {
      clearTimeout(longPressTimer);
      longPressTimer = null;
    }

    cards.forEach(function (card) {
      card.addEventListener('dblclick', function () {
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
      card.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openEditor(card);
        }
      });
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

    if (form) {
      form.addEventListener('submit', function () {
        if (activeCard) {
          cancelLongPress();
        }
      });
    }
  }

  initDiaryImportProgress();
  initDiarySyncProgress();
  initDiaryQuickMenu();
  initDiaryEntryEditor();
}());
