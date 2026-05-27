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
})();
