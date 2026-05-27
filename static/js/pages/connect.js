(function () {
  function initConnectTabs() {
    let connectShell = document.querySelector('[data-connect-shell]');
    if (!connectShell) return;

    let pending = false;

    function renderSkeleton() {
      const rows = 4;
      let html = '<div class="connect-grid connect-skeleton" aria-hidden="true">';
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

    function swapShell(html) {
      const nextHtml = String(html || '').trim();
      if (!nextHtml) return;
      connectShell.outerHTML = nextHtml;
      connectShell = document.querySelector('[data-connect-shell]');
    }

    async function loadTab(url, pushUrl) {
      if (pending) return;
      pending = true;
      try {
        const requestUrl = new URL(url, window.location.href);
        requestUrl.searchParams.set('partial', '1');
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
        swapShell(data.html);
        if (pushUrl) {
          window.history.pushState({ connectUrl: url }, '', url);
        }
      } catch (_) {
        window.location.href = url;
      } finally {
        pending = false;
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
      // show skeleton immediately to improve perceived speed
      try {
        connectShell.innerHTML = renderSkeleton();
      } catch (e) {
        // ignore
      }
      loadTab(href, true);
    });

    window.addEventListener('popstate', function () {
      loadTab(window.location.href, false);
    });
  }

  initConnectTabs();
}());
