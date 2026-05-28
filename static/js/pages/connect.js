(function () {
  function initConnectTabs() {
    let connectShell = document.querySelector('[data-connect-shell]');
    if (!connectShell) return;

    let currentRequestController = null;
    let activeRequestToken = 0;

    function getBodyElement() {
      return connectShell ? connectShell.querySelector('[data-connect-body]') : null;
    }

    function getExternalTabsElement() {
      return connectShell ? connectShell.querySelector('[data-connect-external-tabs]') : null;
    }

    function setActiveTab(link) {
      const tabGroup = link ? link.parentElement : null;
      if (!tabGroup) return;
      const groupTabs = tabGroup.querySelectorAll('[data-connect-tab][role="tab"]');
      groupTabs.forEach(function (tab) {
        const isActive = tab === link;
        tab.classList.toggle('active', isActive);
        tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
      });
    }

    function renderSkeleton() {
      const rows = 4;
      let html = '<div class="connect-summary connect-summary--skeleton"><span class="connect-summary-line"></span></div>';
      html += '<div class="connect-grid connect-skeleton" aria-hidden="true">';
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

    function swapBody(html) {
      const body = getBodyElement();
      if (!body) return;
      const nextHtml = String(html || '').trim();
      if (!nextHtml) return;
      body.innerHTML = nextHtml;
    }

    function swapExternalTabs(html) {
      const tabs = getExternalTabsElement();
      if (!tabs) return;
      const nextHtml = String(html || '').trim();
      if (!nextHtml) return;
      tabs.outerHTML = nextHtml;
    }

    function setBodySkeleton() {
      const body = getBodyElement();
      if (!body) return;
      body.innerHTML = renderSkeleton();
    }

    function syncActiveTabsFromUrl(url) {
      const nextUrl = new URL(url, window.location.href);
      const roleLink = connectShell.querySelector('.connect-tabs--role [data-connect-tab][href="' + nextUrl.pathname + '?role=' + nextUrl.searchParams.get('role') + '&external=' + nextUrl.searchParams.get('external') + '"]');
      const externalLink = connectShell.querySelector('.connect-tabs--external [data-connect-tab][href="' + nextUrl.pathname + '?role=' + nextUrl.searchParams.get('role') + '&external=' + nextUrl.searchParams.get('external') + '"]');
      if (roleLink) setActiveTab(roleLink);
      if (externalLink) setActiveTab(externalLink);
    }

    function extractBodyHtml(html) {
      const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
      const body = doc.querySelector('[data-connect-body]');
      return body ? body.innerHTML : '';
    }

    function extractExternalTabsHtml(html) {
      const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
      const tabs = doc.querySelector('[data-connect-external-tabs]');
      return tabs ? tabs.outerHTML : '';
    }

    async function loadTab(url, pushUrl, token) {
      if (currentRequestController) {
        currentRequestController.abort();
      }
      const controller = new AbortController();
      currentRequestController = controller;
      try {
        const requestUrl = new URL(url, window.location.href);
        requestUrl.searchParams.set('partial', '1');
        const response = await fetch(requestUrl.toString(), {
          credentials: 'same-origin',
          signal: controller.signal,
          headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          }
        });
        if (controller.signal.aborted || token !== activeRequestToken) return;
        const data = await response.json().catch(() => null);
        if (!response.ok || !data || data.ok === false || !data.html) {
          window.location.href = url;
          return;
        }
        if (token !== activeRequestToken) return;
        swapExternalTabs(extractExternalTabsHtml(data.html));
        swapBody(extractBodyHtml(data.html));
        if (pushUrl) {
          window.history.pushState({ connectUrl: url }, '', url);
        }
      } catch (_) {
        if (!controller.signal.aborted) {
          window.location.href = url;
        }
      } finally {
        if (currentRequestController === controller) {
          currentRequestController = null;
        }
      }
    }

    document.addEventListener('click', function (event) {
      const target = event.target;
      if (!(target instanceof Element)) return;

      const link = target.closest('[data-connect-tab]');
      if (!link || !connectShell || !connectShell.contains(link)) return;

      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }

      const href = link.getAttribute('href');
      if (!href) return;

      event.preventDefault();
      activeRequestToken += 1;
      syncActiveTabsFromUrl(href);
      // show skeleton immediately to improve perceived speed
      try {
        setBodySkeleton();
      } catch (e) {
        // ignore
      }
      window.requestAnimationFrame(function () {
        loadTab(href, true, activeRequestToken);
      });
    });

    window.addEventListener('popstate', function () {
      const currentUrl = new URL(window.location.href);
      const activeLink = connectShell.querySelector('[data-connect-tab][href="' + currentUrl.pathname + currentUrl.search + '"]');
      if (activeLink) {
        setActiveTab(activeLink);
      }
      syncActiveTabsFromUrl(window.location.href);
      activeRequestToken += 1;
      setBodySkeleton();
      window.requestAnimationFrame(function () {
        loadTab(window.location.href, false, activeRequestToken);
      });
    });
  }

  initConnectTabs();
}());
