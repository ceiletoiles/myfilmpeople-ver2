(function () {
  function closest(el, sel) {
    if (!el) return null;
    return el.closest(sel);
  }

  function splitWords(value) {
    return String(value || '')
      .split(/\s+/)
      .map(function (s) {
        return s.trim();
      })
      .filter(Boolean);
  }

  function setFilmographyFilter(filterKey) {
    var list = document.getElementById('filmographyList');
    var filters = document.getElementById('filmographyFilters');
    if (!list || !filters) return;

    var key = filterKey || 'all';

    filters.querySelectorAll('button.filter-btn').forEach(function (btn) {
      var isActive = btn.getAttribute('data-filter') === key;
      btn.classList.toggle('active', isActive);
    });

    list.querySelectorAll('[data-filmography-item]').forEach(function (row) {
      var depts = splitWords(row.getAttribute('data-depts'));
      var matches = key === 'all' || depts.indexOf(key) >= 0;
      row.style.display = matches ? '' : 'none';
      if (!matches) return;

      // Toggle role text for this filter.
      row.querySelectorAll('[data-role]').forEach(function (span) {
        span.style.display = 'none';
      });
      var roleSpan = row.querySelector('[data-role="' + key + '"]');
      if (!roleSpan) roleSpan = row.querySelector('[data-role="all"]');
      if (roleSpan) roleSpan.style.display = 'inline-block';
    });
  }

  function initFilmographyFilters() {
    var list = document.getElementById('filmographyList');
    var filters = document.getElementById('filmographyFilters');
    if (!list || !filters) return;
    var defaultKey = list.getAttribute('data-default-filter') || 'all';
    setFilmographyFilter(defaultKey);

    filters.addEventListener('click', function (e) {
      var btn = closest(e.target, 'button.filter-btn');
      if (!btn) return;
      var key = btn.getAttribute('data-filter') || 'all';
      setFilmographyFilter(key);
    });
  }

  function setPersonSection(sectionKey) {
    var tabs = document.querySelectorAll('[data-person-section-tab]');
    var panels = document.querySelectorAll('[data-person-section-panel]');
    if (!tabs.length || !panels.length) return;

    var activeKey = sectionKey || 'filmography';

    tabs.forEach(function (tab) {
      var isActive = tab.getAttribute('data-person-section-tab') === activeKey;
      tab.classList.toggle('active', isActive);
      tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    panels.forEach(function (panel) {
      var isActive = panel.getAttribute('data-person-section-panel') === activeKey;
      panel.hidden = !isActive;
      panel.classList.toggle('is-active', isActive);
    });
  }

  function initPersonSections() {
    var tabs = document.querySelectorAll('[data-person-section-tab]');
    var panels = document.querySelectorAll('[data-person-section-panel]');
    if (!tabs.length || !panels.length) return;

    setPersonSection('filmography');

    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        setPersonSection(tab.getAttribute('data-person-section-tab') || 'filmography');
      });
    });
  }

  function closeAllKebabMenus(exceptDetails) {
    document.querySelectorAll('details[data-kebab-menu][open]').forEach(function (d) {
      if (exceptDetails && d === exceptDetails) return;
      d.removeAttribute('open');
    });
  }

  // Init ASAP (script is loaded at end of body).
  initFilmographyFilters();
  initPersonSections();

  // Also init on DOMContentLoaded as a fallback.
  document.addEventListener('DOMContentLoaded', function () {
    initFilmographyFilters();
    initPersonSections();
  });

  document.addEventListener('click', function (e) {
    var target = e.target;

    // Bio toggle
    var bioToggle = closest(target, '[data-bio-toggle]');
    if (bioToggle) {
      var bioRoot = closest(bioToggle, '[data-bio]');
      if (!bioRoot) return;
      var expanded = bioRoot.getAttribute('data-expanded') === '1';
      bioRoot.setAttribute('data-expanded', expanded ? '0' : '1');
      bioToggle.textContent = expanded ? 'Show more' : 'Show less';
      return;
    }

    // Open dialog
    var openBtn = closest(target, '[data-open-dialog]');
    if (openBtn) {
      var selector = openBtn.getAttribute('data-open-dialog');
      if (!selector) return;
      var dialog = document.querySelector(selector);
      if (dialog && typeof dialog.showModal === 'function') {
        try {
          dialog.showModal();
        } catch (_) {
          // If already open or unsupported, ignore.
        }
      }
      var menu = closest(openBtn, 'details[data-kebab-menu]');
      if (menu) menu.removeAttribute('open');
      return;
    }

    // Close kebab menus when clicking outside
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
