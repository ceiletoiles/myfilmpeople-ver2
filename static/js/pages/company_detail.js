(function () {
  function closest(el, sel) {
    if (!el) return null;
    return el.closest(sel);
  }

  function closeAllKebabMenus(exceptDetails) {
    document.querySelectorAll('details[data-kebab-menu][open]').forEach(function (d) {
      if (exceptDetails && d === exceptDetails) return;
      d.removeAttribute('open');
    });
  }

  document.addEventListener('click', function (e) {
    var target = e.target;

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
          // ignore
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
