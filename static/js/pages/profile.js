(function () {
  const storageKey = 'myfilmpeople.followingTab';
  const profileViewStorageKey = 'myfilmpeople.profileView';

  function getTabs() {
    return Array.from(document.querySelectorAll('input[name="following-tab"]'));
  }

  function updateFollowingCount() {
    const followingCountEl = document.querySelector('[data-following-count]');
    if (!followingCountEl) return;

    const tabs = getTabs();
    const checked = tabs.find((tab) => tab.checked) || tabs[0];
    if (!checked) return;

    const label = document.querySelector(`label[for="${checked.id}"]`);
    const countText = (label?.dataset.tabCount || '0').trim();
    followingCountEl.textContent = countText === '0' ? '' : countText;
  }

  function restoreActiveTab() {
    const tabs = getTabs();
    if (tabs.length === 0) return;

    const savedTabId = window.sessionStorage.getItem(storageKey);
    const target = savedTabId ? tabs.find((tab) => tab.id === savedTabId) : null;
    if (target) {
      target.checked = true;
    } else if (!tabs.some((tab) => tab.checked)) {
      tabs[0].checked = true;
    }

    const checkedTab = tabs.find((tab) => tab.checked);
    if (checkedTab) {
      window.sessionStorage.setItem(storageKey, checkedTab.id);
    }

    updateFollowingCount();
  }

  function closeStatusMenus() {
    document.querySelectorAll('.status-filter-menu').forEach((menu) => {
      menu.open = false;
    });
  }

  function renderProfileSkeleton() {
    const rows = 3;
    let html = '<div class="activity-list profile-skeleton" aria-hidden="true">';
    for (let i = 0; i < rows; i++) {
      html += '<div class="skeleton-row">'
        + '<div class="skeleton-avatar"></div>'
        + '<div class="skeleton-lines">'
          + '<div class="skeleton-line skeleton-line-short"></div>'
          + '<div class="skeleton-line"></div>'
        + '</div>'
      + '</div>';
    }
    html += '</div>';
    return html;
  }

  function getProfileView() {
    return window.sessionStorage.getItem(profileViewStorageKey) === 'activity' ? 'activity' : 'overview';
  }

  function setProfileView(view) {
    const normalizedView = view === 'activity' ? 'activity' : 'overview';
    const shell = document.querySelector('[data-profile-shell]');
    const toggle = document.querySelector('[data-profile-view-toggle]');
    const label = document.querySelector('[data-profile-view-label]');
    const sections = Array.from(document.querySelectorAll('[data-profile-section]'));

    if (shell) {
      shell.dataset.profileView = normalizedView;
    }

    sections.forEach((section) => {
      const sectionView = section.getAttribute('data-profile-section');
      section.hidden = sectionView !== normalizedView;
    });

    if (toggle) {
      toggle.setAttribute('aria-pressed', String(normalizedView === 'activity'));
    }

    if (label) {
      label.textContent = normalizedView === 'activity' ? 'Profile' : 'Activity';
    }

    window.sessionStorage.setItem(profileViewStorageKey, normalizedView);
  }

  function restoreProfileView() {
    setProfileView(getProfileView());
  }

  function toggleProfileView() {
    setProfileView(getProfileView() === 'activity' ? 'overview' : 'activity');
    closeStatusMenus();
  }

  function replaceProfileShell(html) {
    const nextHtml = String(html || '').trim();
    if (!nextHtml) return false;

    const currentShell = document.querySelector('[data-profile-shell]');
    if (!currentShell) return false;

    currentShell.outerHTML = nextHtml;
    restoreActiveTab();
    restoreProfileView();
    closeStatusMenus();
    return true;
  }

  async function loadProfileState(url, pushUrl) {
    const requestUrl = new URL(url, window.location.href);
    requestUrl.searchParams.set('partial', '1');

    try {
      const response = await fetch(requestUrl.toString(), {
        credentials: 'same-origin',
        headers: {
          'Accept': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        }
      });

      const data = await response.json().catch(() => null);
      if (!response.ok || !data || data.ok === false || !data.html) {
        window.location.href = url;
        return;
      }

      if (!replaceProfileShell(data.html)) {
        window.location.href = url;
        return;
      }

      if (pushUrl) {
        window.history.pushState({ profileUrl: url }, '', url);
      }
    } catch (_) {
      window.location.href = url;
    }
  }

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const viewToggle = target.closest('[data-profile-view-toggle]');
    if (viewToggle) {
      event.preventDefault();
      toggleProfileView();
      return;
    }

    // activity pagination links (ajax)
    const paginateLink = target.closest('[data-profile-paginate]');
    if (paginateLink) {
      const href = paginateLink.getAttribute('href');
      if (!href) return;
      event.preventDefault();
      try {
        const list = document.querySelector('.profile-section.profile-activity .activity-list');
        if (list) list.outerHTML = renderProfileSkeleton();
      } catch (e) {
        // ignore
      }
      loadProfileState(href, true);
      return;
    }

    const statusLink = target.closest('[data-profile-status-option]');
    if (!statusLink) return;

    const href = statusLink.getAttribute('href');
    if (!href) return;

    event.preventDefault();
    // show a quick skeleton while the partial loads
    try {
      const list = document.querySelector('.profile-section.profile-activity .activity-list');
      if (list) list.outerHTML = renderProfileSkeleton();
    } catch (e) {
      // ignore render errors
    }
    loadProfileState(href, true);
  });

  document.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.name !== 'following-tab') return;

    if (target.checked) {
      window.sessionStorage.setItem(storageKey, target.id);
      updateFollowingCount();
    }
  });

  document.addEventListener('pointerdown', (event) => {
    const statusMenus = Array.from(document.querySelectorAll('.status-filter-menu'));
    if (statusMenus.length === 0) return;

    const target = event.target instanceof Node ? event.target : null;
    if (!target) return;

    const clickedInsideMenu = statusMenus.some((menu) => menu.contains(target));
    if (!clickedInsideMenu) {
      closeStatusMenus();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeStatusMenus();
    }
  });

  window.addEventListener('popstate', () => {
    loadProfileState(window.location.href, false);
  });

  document.addEventListener('DOMContentLoaded', () => {
    restoreActiveTab();
    restoreProfileView();
    closeStatusMenus();
  });

  // Server-persisted badge notifications are used; polling removed to avoid frequent follow_status calls.

  // Badge modal behavior
  function closeBadgeModal(overlay) {
    if (!overlay) return;
    overlay.remove();
    document.removeEventListener('keydown', escHandler);
  }

  function escHandler(e) {
    if (e.key === 'Escape') {
      const overlay = document.querySelector('.badge-modal-overlay');
      if (overlay) closeBadgeModal(overlay);
    }
  }

  function showBadgeModal(opts) {
    const { username, minCount, label, imgSrc } = opts || {};
    if (!username || !minCount) return;
    // remove existing
    const existing = document.querySelector('.badge-modal-overlay');
    if (existing) existing.remove();

    const openedAt = Date.now();
    const overlay = document.createElement('div');
    overlay.className = 'badge-modal-overlay';
    const baseSentence = `${username} earned this badge`;
    const min = Number(minCount || 0);
    const detailSentence = `A mark of true cinephile dedication, awarded for following ${min} or more people in the film industry.`;
    overlay.innerHTML = `
      <div class="badge-modal" role="dialog" aria-modal="true" aria-label="Badge details">
        <img src="${imgSrc || ''}" alt="${label || 'Badge'}" />
        <h3>${baseSentence}</h3>
        <p>${detailSentence}</p>
        <button class="badge-modal-close" type="button">Close</button>
      </div>
    `;

    overlay.addEventListener('click', function (e) {
      if (Date.now() - openedAt < 500) return;
      if (e.target === overlay) closeBadgeModal(overlay);
    });

    overlay.querySelector('.badge-modal-close')?.addEventListener('click', function () {
      closeBadgeModal(overlay);
    });

    document.body.appendChild(overlay);
    document.addEventListener('keydown', escHandler);
  }
  // Expose a minimal hook so server-rendered templates can trigger the modal on page load.
  try { window.mfpShowBadgeModal = showBadgeModal; } catch (e) { /* ignore */ }
  // debounce to avoid double-opening on touch -> click sequence
  let _lastBadgeOpenAt = 0;
  function handleBadgeActivation(badge) {
    if (!badge) return;
    const now = Date.now();
    if (now - _lastBadgeOpenAt < 600) return; // ignore rapid duplicates
    _lastBadgeOpenAt = now;
    const username = badge.dataset.badgeUsername || '';
    const minCount = badge.dataset.badgeMinCount || '';
    const label = badge.dataset.badgeLabel || '';
    const img = badge.querySelector('.profile-badge-image');
    const imgSrc = img ? img.getAttribute('src') : '';
    showBadgeModal({ username, minCount, label, imgSrc });
  }

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const badge = target.closest('.js-profile-badge');
    if (!badge) return;
    event.preventDefault();
    event.stopPropagation();
    handleBadgeActivation(badge);
  });

  // Ensure touch taps also activate reliably on mobile (pointer events)
  document.addEventListener('pointerup', (event) => {
    try {
      if (event.pointerType !== 'touch' && event.pointerType !== 'pen') return;
    } catch (e) {
      // defensive: if pointerType is unavailable, ignore
    }
    const target = event.target;
    if (!(target instanceof Element)) return;
    const badge = target.closest('.js-profile-badge');
    if (!badge) return;
    event.preventDefault();
    event.stopPropagation();
    handleBadgeActivation(badge);
  });

  document.addEventListener('keydown', (event) => {
    const el = event.target instanceof Element ? event.target : null;
    if (!el) return;
    if (el.classList.contains('js-profile-badge') && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      el.click();
    }
  });
})();
