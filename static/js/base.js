(function () {

  function initSyncProgress() {
    const progressWrap = document.querySelector('[data-sync-progress]');
    const textEl = document.querySelector('[data-sync-progress-text]');
    const barRoot = document.querySelector('[data-sync-progress-bar]');
    const barFill = document.querySelector('[data-sync-progress-fill]');

    if (!progressWrap || !textEl || !barRoot || !barFill) return;

    let pollTimer = null;
    let activeProgressUrl = null;
    let activeButton = null;

    function setHidden(hidden) {
      progressWrap.hidden = !!hidden;
    }

    function setText(msg) {
      textEl.textContent = msg || '';
    }

    function setPercent(pct) {
      const p = Math.max(0, Math.min(100, Number(pct || 0)));
      barFill.style.width = p.toFixed(1) + '%';
      barRoot.setAttribute('aria-valuenow', String(Math.round(p)));
    }

    function setRunning(running) {
      if (running) barRoot.classList.add('is-running');
      else barRoot.classList.remove('is-running');
    }

    function clearPolling() {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function computePercent(data) {
      const totalEntities = Number(data.total_entities || 0);
      if (!totalEntities) return 100;

      const donePeople = Number(data.synced_people || 0) + Number(data.fail_people || 0);
      const doneCompanies = Number(data.synced_companies || 0) + Number(data.fail_companies || 0);
      const doneEntities = donePeople + doneCompanies;

      const subTotal = Number(data.current_sub_total || 0);
      const subDone = Number(data.current_sub_done || 0);
      const subFrac = subTotal > 0 ? Math.max(0, Math.min(1, subDone / subTotal)) : 0;

      return ((doneEntities + subFrac) / totalEntities) * 100;
    }

    function formatLine(data) {
      const totalEntities = Number(data.total_entities || 0);
      const totalPeople = Number(data.total_people || 0);
      const totalCompanies = Number(data.total_companies || 0);

      const syncedPeople = Number(data.synced_people || 0);
      const syncedCompanies = Number(data.synced_companies || 0);
      const failPeople = Number(data.fail_people || 0);
      const failCompanies = Number(data.fail_companies || 0);

      const donePeople = syncedPeople + failPeople;
      const doneCompanies = syncedCompanies + failCompanies;
      const doneEntities = donePeople + doneCompanies;
      const leftEntities = Math.max(0, totalEntities - doneEntities);

      const subTotal = Number(data.current_sub_total || 0);
      const subDone = Number(data.current_sub_done || 0);
      const subLeft = subTotal > 0 ? Math.max(0, subTotal - subDone) : 0;

      const notif = Number(data.notifications_created || 0);
      const fail = failPeople + failCompanies;
      const label = (data.current_label || '').trim();

      const parts = [];
      if (label) parts.push(label);

      if (totalPeople) {
        parts.push('People ' + donePeople + '/' + totalPeople);
      }
      if (totalCompanies) {
        parts.push('Companies ' + doneCompanies + '/' + totalCompanies);
      }
      if (!totalPeople && !totalCompanies && totalEntities) {
        parts.push('Synced ' + doneEntities + '/' + totalEntities + ' (left ' + leftEntities + ')');
      } else if (totalEntities) {
        parts.push('Left ' + leftEntities);
      }

      if (subTotal > 0) {
        parts.push('Pages ' + subDone + '/' + subTotal + ' (left ' + subLeft + ')');
      }

      parts.push('Notifications ' + notif);
      if (fail) parts.push('Failed ' + fail);
      return parts.join(' • ');
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
          return;
        }

        const pct = computePercent(data);
        setPercent(pct);
        setText(formatLine(data));

        const status = String(data.status || '');
        if (status === 'done' || status === 'done_with_errors') {
          setPercent(100);
          setRunning(false);
          clearPolling();
          if (activeButton) activeButton.disabled = false;
          // Auto-hide shortly after completion.
          window.setTimeout(function () {
            setHidden(true);
          }, 1400);
        }
      } catch (_) {
        clearPolling();
        if (activeButton) activeButton.disabled = false;
        setRunning(false);
        setText('Network error while syncing.');
      }
    }

    async function startJob(form) {
      const startUrl = form.getAttribute('data-sync-job-start-url') || form.getAttribute('data-sync-all-start-url') || '';
      if (!startUrl) return;

      const btn = form.querySelector('button[type="submit"], button:not([type])');
      activeButton = btn;

      clearPolling();
      setHidden(false);
      setRunning(true);
      setPercent(3);
      setText('Starting…');
      if (activeButton) activeButton.disabled = true;

      try {
        const resp = await fetch(startUrl, {
          method: 'POST',
          credentials: 'same-origin',
          body: new FormData(form),
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          }
        });
        const data = await resp.json().catch(() => null);
        if (!resp.ok || !data || data.ok === false) {
          if (activeButton) activeButton.disabled = false;
          setText((data && (data.error || data.message)) || ('Sync failed (' + resp.status + ')'));
          return;
        }

        if (data.status === 'done') {
          setPercent(100);
          setRunning(false);
          setText(data.message || 'Nothing to sync.');
          if (activeButton) activeButton.disabled = false;
          window.setTimeout(function () {
            setHidden(true);
          }, 1200);
          return;
        }

        activeProgressUrl = data.progress_url || null;
        if (!activeProgressUrl) {
          if (activeButton) activeButton.disabled = false;
          setRunning(false);
          setText('Could not start sync progress.');
          return;
        }

        await pollOnce();
        pollTimer = window.setInterval(pollOnce, 900);
      } catch (_) {
        if (activeButton) activeButton.disabled = false;
        setRunning(false);
        setText('Network error.');
      }
    }

    document.addEventListener('submit', function (e) {
      const form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (!(form.matches('form[data-sync-all-form]') || form.matches('form[data-sync-job-form]'))) return;
      e.preventDefault();
      startJob(form);
    });
  }

  function initMessageToasts() {
    const container = document.querySelector('.messages');
    if (!container) return;

    container.classList.add('messages--toast');

    let dismissed = false;
    function dismiss() {
      if (dismissed) return;
      dismissed = true;
      container.classList.add('messages--hide');
      window.setTimeout(function () {
        if (container && container.parentNode) {
          container.parentNode.removeChild(container);
        }
      }, 220);
    }

    container.addEventListener('click', dismiss);
    window.setTimeout(dismiss, 4500);
  }

  function initHamburgerMenu() {
    const toggleBtn = document.querySelector('[data-menu-toggle]');
    const drawer = document.querySelector('[data-menu-drawer]');
    const overlay = document.querySelector('[data-menu-overlay]');
    const closeBtn = document.querySelector('[data-menu-close-btn]');

    if (!toggleBtn || !drawer || !overlay) return;

    let lastFocused = null;

    function isOpen() {
      return document.body.classList.contains('menu-open');
    }

    function openMenu() {
      if (isOpen()) return;
      lastFocused = document.activeElement;
      drawer.hidden = false;
      overlay.hidden = false;
      requestAnimationFrame(function () {
        document.body.classList.add('menu-open');
      });
      toggleBtn.setAttribute('aria-expanded', 'true');
    }

    function closeMenu() {
      if (!isOpen()) return;
      document.body.classList.remove('menu-open');
      toggleBtn.setAttribute('aria-expanded', 'false');

      const finish = function () {
        drawer.hidden = true;
        overlay.hidden = true;
        drawer.removeEventListener('transitionend', finish);
      };

      drawer.addEventListener('transitionend', finish);

      // Fallback in case transitionend doesn't fire
      window.setTimeout(function () {
        if (!isOpen()) {
          drawer.hidden = true;
          overlay.hidden = true;
        }
      }, 260);

      if (lastFocused && typeof lastFocused.focus === 'function') {
        lastFocused.focus();
      }
      lastFocused = null;
    }

    toggleBtn.addEventListener('click', function () {
      if (isOpen()) closeMenu();
      else openMenu();
    });

    overlay.addEventListener('click', closeMenu);

    if (closeBtn) {
      closeBtn.addEventListener('click', closeMenu);
    }

    drawer.addEventListener('click', function (e) {
      const target = e.target;
      if (!(target instanceof Element)) return;
      if (target.matches('[data-menu-close]')) {
        closeMenu();
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && isOpen()) {
        e.preventDefault();
        closeMenu();
      }
    });
  }

  function initSearchPrompt() {
    const triggers = Array.from(document.querySelectorAll('[data-search-open]'));
    const overlay = document.querySelector('[data-search-overlay]');
    const prompt = document.querySelector('[data-search-prompt]');
    const closeBtn = document.querySelector('[data-search-close]');
    const form = document.querySelector('[data-search-form]');
    const input = document.querySelector('[data-search-input]');

    if (!triggers.length || !overlay || !prompt || !form || !input) return;

    let lastFocused = null;

    function isOpen() {
      return document.body.classList.contains('search-open');
    }

    function openSearch() {
      lastFocused = document.activeElement;
      overlay.hidden = false;
      prompt.hidden = false;
      requestAnimationFrame(function () {
        document.body.classList.add('search-open');
      });
      window.setTimeout(function () {
        input.focus();
        input.select();
      }, 0);
    }

    function closeSearch() {
      if (!isOpen()) return;
      document.body.classList.remove('search-open');

      const finish = function () {
        overlay.hidden = true;
        prompt.hidden = true;
        prompt.removeEventListener('transitionend', finish);
      };

      prompt.addEventListener('transitionend', finish);
      window.setTimeout(function () {
        if (!isOpen()) {
          overlay.hidden = true;
          prompt.hidden = true;
        }
      }, 260);

      if (lastFocused && typeof lastFocused.focus === 'function') {
        lastFocused.focus();
      }
      lastFocused = null;
    }

    for (const trigger of triggers) {
      trigger.addEventListener('click', function (e) {
        e.preventDefault();
        openSearch();
      });
    }

    overlay.addEventListener('click', closeSearch);

    if (closeBtn) {
      closeBtn.addEventListener('click', closeSearch);
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      const q = (input.value || '').trim();
      if (!q) {
        input.focus();
        return;
      }
      window.location.href = form.action + '?q=' + encodeURIComponent(q);
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && isOpen()) {
        e.preventDefault();
        closeSearch();
      }
    });
  }

  function findStatusEl(root) {
    if (!root) return null;
    return root.querySelector('[data-ajax-status]');
  }

  function setStatusBySelector(selector, msg) {
    if (!selector) return false;
    const el = document.querySelector(selector);
    if (!el) return false;
    const messageEl = el.matches('.msg, [data-ajax-status]') ? el : el.querySelector('.msg, [data-ajax-status]');
    const target = messageEl || el;
    target.textContent = msg || '';
    if (el.hasAttribute('hidden')) {
      el.removeAttribute('hidden');
    }
    return true;
  }

  function showPageMessage(msg) {
    let container = document.querySelector('.page-inline-messages');
    if (!container) {
      const main = document.querySelector('main');
      if (!main) return false;
      container = document.createElement('div');
      container.className = 'messages page-inline-messages';
      const msgEl = document.createElement('div');
      msgEl.className = 'msg';
      container.appendChild(msgEl);

      const syncProgress = main.querySelector('.sync-progress');
      if (syncProgress && syncProgress.parentNode === main) {
        syncProgress.insertAdjacentElement('afterend', container);
      } else {
        main.insertBefore(container, main.firstChild);
      }
    }

    const msgEl = container.querySelector('.msg');
    if (msgEl) {
      msgEl.textContent = msg || '';
    }
    container.hidden = false;
    return true;
  }

  function setStatus(root, msg) {
    const el = findStatusEl(root);
    if (el) {
      el.textContent = msg || '';
    }
  }

  function firstSubmitButton(form) {
    return form.querySelector('button[type="submit"], button:not([type])');
  }

  document.addEventListener('submit', async function (e) {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches('form[data-ajax="1"]')) return;

    e.preventDefault();

    const btn = firstSubmitButton(form);
    if (btn) btn.disabled = true;
    setStatus(form, 'Working...');

    try {
      const resp = await fetch(form.action, {
        method: (form.method || 'POST').toUpperCase(),
        body: new FormData(form),
        headers: {
          'Accept': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        }
      });

      let data = null;
      try {
        data = await resp.json();
      } catch (_) {
        data = null;
      }

      if (!resp.ok || !data || data.ok === false) {
        const msg = (data && (data.error || data.message)) || ('Request failed (' + resp.status + ')');
        setStatus(form, msg);
        if (btn) btn.disabled = false;
        return;
      }

      if (data.controls_target && typeof data.controls_html === 'string') {
        const target = document.querySelector(data.controls_target);
        if (target) {
          target.innerHTML = data.controls_html;
          if (data.message && !setStatusBySelector(data.status_target, data.message)) {
            showPageMessage(data.message);
          }
        }
        return;
      }

      if (form.dataset.ajaxAction === 'follow-search') {
        if (btn) {
          btn.textContent = 'Following';
          btn.disabled = true;
          btn.classList.add('secondary');
        }
      }

      if (data.message) {
        if (!setStatusBySelector(data.status_target, data.message)) {
          showPageMessage(data.message);
        }
      } else {
        showPageMessage('Done.');
      }
    } catch (err) {
      showPageMessage('Network error.');
      if (btn) btn.disabled = false;
    }
  });

  initHamburgerMenu();
  initSearchPrompt();
  initMessageToasts();
  initSyncProgress();
})();
