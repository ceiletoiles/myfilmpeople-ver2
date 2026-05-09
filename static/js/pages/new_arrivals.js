(function () {
  const modeToggle = document.querySelector('[data-history-toggle]');
  const modeTagline = document.querySelector('[data-mode-tagline]');
  const tabsGroups = Array.from(document.querySelectorAll('[data-tabs-group]'));
  const tabs = Array.from(document.querySelectorAll('.new-tab'));
  const panels = Array.from(document.querySelectorAll('.new-panel'));
  const modal = document.getElementById('new-studio-modal');
  const modalBody = document.getElementById('new-studio-modal-body');
  const modalTitle = document.getElementById('new-studio-modal-title');
  const modalCount = document.getElementById('new-studio-modal-count');
  const studioButtons = Array.from(document.querySelectorAll('.new-studio-card'));

  if (!tabs.length || !panels.length) {
    return;
  }

  let mode = 'new';
  const lastTabByMode = {
    new: 'all',
    history: 'all',
  };

  function activeTabs() {
    return tabs.filter(function (tab) {
      const group = tab.closest('[data-tabs-group]');
      return group && !group.hidden;
    });
  }

  function currentTabsGroup() {
    return tabsGroups.find(function (group) {
      return group.dataset.tabsGroup === mode;
    }) || null;
  }

  function updateTabState(tabName) {
    const visibleTabs = activeTabs();
    for (const tab of visibleTabs) {
      const isActive = tab.dataset.tab === tabName;
      const hasContent = Number(tab.dataset.count || 0) > 0;
      tab.classList.toggle('is-active', isActive);
      tab.classList.toggle('has-content', hasContent);
      tab.setAttribute('aria-disabled', hasContent ? 'false' : 'true');
    }
  }

  function firstContentTab(tabGroup) {
    if (!tabGroup) {
      return null;
    }
    const tabsInOrder = Array.from(tabGroup.querySelectorAll('.new-tab'));
    const preferred = tabsInOrder.find(function (tab) {
      return tab.dataset.tab !== 'all' && Number(tab.dataset.count || 0) > 0;
    });
    if (preferred) {
      return preferred;
    }
    return tabsInOrder.find(function (tab) {
      return Number(tab.dataset.count || 0) > 0;
    }) || null;
  }

  function setPanelState(tabName) {
    for (const panel of panels) {
      const isModeMatch = panel.dataset.modePanel === mode;
      const isActive = isModeMatch && panel.dataset.panel === tabName;
      panel.classList.toggle('is-active', isActive);
      panel.hidden = !isActive;
    }
  }

  function setMode(newMode) {
    mode = newMode === 'history' ? 'history' : 'new';

    for (const group of tabsGroups) {
      const isCurrent = group.dataset.tabsGroup === mode;
      group.hidden = !isCurrent;
    }

    if (modeTagline) {
      modeTagline.textContent = mode === 'history'
        ? 'History from the last year, including what you already opened.'
        : 'Recently discovered work from the people and studios you follow.';
    }

    if (modeToggle) {
      modeToggle.setAttribute('aria-pressed', mode === 'history' ? 'true' : 'false');
      modeToggle.setAttribute('aria-label', mode === 'history' ? 'Show new arrivals' : 'Show history');
    }

    const activeGroup = currentTabsGroup();
    const rememberedTab = lastTabByMode[mode] || 'all';
    const targetTabElement = activeGroup ? activeGroup.querySelector('.new-tab[data-tab="' + rememberedTab + '"]') : null;
    const fallbackTab = firstContentTab(activeGroup);
    const targetTab = (targetTabElement && Number(targetTabElement.dataset.count || 0) > 0)
      ? (targetTabElement.dataset.tab || 'all')
      : (fallbackTab ? (fallbackTab.dataset.tab || 'all') : 'all');
    lastTabByMode[mode] = targetTab;
    updateTabState(targetTab);
    setPanelState(targetTab);
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
    if (!modal || !modalBody || !modalTitle || !modalCount) {
      return;
    }

    const templateId = button.dataset.studioTemplate || '';
    const template = templateId ? document.getElementById(templateId) : null;

    modalTitle.textContent = button.dataset.studioName || '';
    modalCount.textContent = button.dataset.studioCount ? (button.dataset.studioCount + ' items') : '';
    modalBody.innerHTML = template ? template.innerHTML : '<p class="muted">No items found.</p>';
    modal.hidden = false;
  }

  for (const tab of tabs) {
    tab.addEventListener('click', function () {
      const count = Number(tab.dataset.count || 0);
      if (count <= 0) {
        return;
      }
      const nextTab = tab.dataset.tab || 'all';
      lastTabByMode[mode] = nextTab;
      setPanelState(nextTab);
      updateTabState(nextTab);
    });
  }

  if (modeToggle) {
    modeToggle.addEventListener('click', function () {
      setMode(mode === 'new' ? 'history' : 'new');
    });
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

  setMode('new');
})();