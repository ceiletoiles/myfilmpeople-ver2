(function () {
  const tabs = Array.from(document.querySelectorAll('.recent-tab'));
  const panels = Array.from(document.querySelectorAll('.recent-panel'));
  const modal = document.getElementById('recent-studio-modal');
  const modalBody = document.getElementById('recent-studio-modal-body');
  const modalCount = document.getElementById('recent-studio-modal-count');
  const modalLogo = document.getElementById('recent-studio-modal-logo');
  const studioButtons = Array.from(document.querySelectorAll('.recent-studio-card'));

  if (!tabs.length || !panels.length) {
    return;
  }

  function setActive(tabName) {
    for (const tab of tabs) {
      const isActive = tab.dataset.tab === tabName;
      tab.classList.toggle('is-active', isActive);
    }

    for (const panel of panels) {
      const isActive = panel.dataset.panel === tabName;
      panel.classList.toggle('is-active', isActive);
      panel.hidden = !isActive;
    }
  }

  for (const tab of tabs) {
    tab.addEventListener('click', function () {
      setActive(tab.dataset.tab || 'all');
    });
  }

  function closeModal() {
    if (!modal) {
      return;
    }
    modal.hidden = true;
    if (modalBody) {
      modalBody.innerHTML = '';
    }
  }

  function openModal(button) {
    if (!modal || !modalBody || !modalCount || !modalLogo) {
      return;
    }

    const templateId = button.dataset.studioTemplate || '';
    const template = templateId ? document.getElementById(templateId) : null;

    modalCount.textContent = button.dataset.studioCount ? (button.dataset.studioCount + ' recent films') : '';
    modalLogo.src = button.dataset.studioLogo || '';
    modalLogo.alt = button.dataset.studioName || '';
    modalBody.innerHTML = template ? template.innerHTML : '<p class="muted">No recent movies found for this studio.</p>';
    modal.hidden = false;
  }

  for (const button of studioButtons) {
    button.addEventListener('click', function () {
      openModal(button);
    });
  }

  if (modal) {
    modal.addEventListener('click', function (event) {
      const target = event.target;
      if (target && target instanceof HTMLElement && target.hasAttribute('data-modal-close')) {
        closeModal();
      }
    });
  }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeModal();
    }
  });

  setActive('all');
})();
