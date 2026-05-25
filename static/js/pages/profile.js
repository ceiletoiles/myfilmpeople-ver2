(function () {
  const storageKey = 'myfilmpeople.followingTab';

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

  function replaceProfileShell(html) {
    const nextHtml = String(html || '').trim();
    if (!nextHtml) return false;

    const currentShell = document.querySelector('[data-profile-shell]');
    if (!currentShell) return false;

    currentShell.outerHTML = nextHtml;
    restoreActiveTab();
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

    const statusLink = target.closest('[data-profile-status-option]');
    if (!statusLink) return;

    const href = statusLink.getAttribute('href');
    if (!href) return;

    event.preventDefault();
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
    closeStatusMenus();
  });
})();
