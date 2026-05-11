(function () {
  const input = document.getElementById('person-query');
  const suggestionsEl = document.getElementById('suggestions');

  if (!input || !suggestionsEl) {
    return;
  }

  const SUGGEST_URL = input.dataset.suggestUrl;
  const PLACEHOLDER_PERSON = input.dataset.placeholderPerson;

  if (!SUGGEST_URL) {
    return;
  }

  function escapeHtml(text) {
    const span = document.createElement('span');
    span.textContent = text;
    return span.innerHTML;
  }

  function ensureSelectedContainer() {
    const container = document.getElementById('selected-people');
    const empty = document.getElementById('selected-empty');
    if (empty) {
      empty.remove();
    }
    return container;
  }

  function addSelectedPerson(id, name, profilePath, knownForDepartment) {
    const container = ensureSelectedContainer();
    const existing = document.getElementById('selected-person-' + id);
    if (existing) {
      return;
    }

    const card = document.createElement('div');
    card.className = 'collab-person-tile';
    card.id = 'selected-person-' + id;

    const hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = 'person_ids';
    hidden.value = String(id);
    card.appendChild(hidden);

    const img = document.createElement('img');
    img.className = 'collab-person-avatar';
    if (profilePath) {
      img.src = 'https://image.tmdb.org/t/p/w185' + profilePath;
    } else {
      img.src = PLACEHOLDER_PERSON || '';
    }
    img.alt = '';
    card.appendChild(img);

    const info = document.createElement('div');
    info.className = 'collab-person-name';
    info.innerHTML = '<strong>' + escapeHtml(name) + '</strong>';
    card.appendChild(info);

    const role = document.createElement('div');
    role.className = 'muted collab-person-role';
    role.textContent = knownForDepartment || 'Unknown';
    card.appendChild(role);

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'collab-person-remove';
    btn.textContent = '×';
    btn.setAttribute('aria-label', 'Remove ' + name);
    btn.dataset.removeSelectedPerson = String(id);
    card.appendChild(btn);
    container.appendChild(card);
  }

  function removeSelectedPerson(id) {
    const el = document.getElementById('selected-person-' + id);
    if (el) {
      el.remove();
    }
    
    // Check if we still have 2+ people selected
    checkAndClearResultsIfNeeded();
  }

  function checkAndClearResultsIfNeeded() {
    const container = document.getElementById('selected-people');
    const inputs = container.querySelectorAll('input[name="person_ids"]');
    const resultsDiv = document.querySelector('.collab-results');
    
    // If fewer than 2 people selected, hide results
    if (inputs.length < 2 && resultsDiv) {
      resultsDiv.style.display = 'none';
    }
  }

  function hideSuggestions() {
    suggestionsEl.classList.remove('active');
    suggestionsEl.innerHTML = '';
  }

  function showSuggestions(items) {
    if (!items.length) {
      hideSuggestions();
      return;
    }
    suggestionsEl.classList.add('active');
    suggestionsEl.innerHTML = '';

    const grid = document.createElement('div');
    grid.className = 'collab-suggestion-grid';

    for (const p of items) {
      const img = document.createElement('img');
      img.className = 'collab-person-avatar';
      img.alt = '';
      if (p.profile_path) {
        img.src = 'https://image.tmdb.org/t/p/w185' + p.profile_path;
      } else {
        img.src = PLACEHOLDER_PERSON || '';
      }

      const tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'collab-suggestion-tile';
      tile.setAttribute('aria-label', 'Add ' + (p.name || 'person'));
      tile.onclick = function () {
        addSelectedPerson(p.id, p.name || '', p.profile_path || '', p.known_for_department || '');
        input.value = '';
        hideSuggestions();
      };

      tile.appendChild(img);

      const nameDiv = document.createElement('div');
      nameDiv.className = 'collab-suggestion-name';
      nameDiv.innerHTML = '<strong>' + escapeHtml(p.name || '') + '</strong>';
      tile.appendChild(nameDiv);

      const meta = document.createElement('div');
      meta.className = 'muted collab-suggestion-role';
      meta.textContent = p.known_for_department || 'Unknown';
      tile.appendChild(meta);

      grid.appendChild(tile);
    }

    suggestionsEl.appendChild(grid);
  }

  async function fetchSuggestions(q) {
    const url = SUGGEST_URL + '?q=' + encodeURIComponent(q);
    const resp = await fetch(url, { headers: { 'Accept': 'application/json' } });
    const data = await resp.json();
    return data.results || [];
  }

  let debounce = null;

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
    }
  });

  input.addEventListener('input', function () {
    const q = (input.value || '').trim();
    if (debounce) {
      clearTimeout(debounce);
    }
    if (q.length < 2) {
      hideSuggestions();
      return;
    }
    debounce = setTimeout(async function () {
      try {
        const items = await fetchSuggestions(q);
        showSuggestions(items);
      } catch (e) {
        hideSuggestions();
      }
    }, 250);
  });

  document.addEventListener('click', function (e) {
    if (!suggestionsEl.contains(e.target) && e.target !== input) {
      hideSuggestions();
    }
  });

  document.addEventListener('click', function (e) {
    const btn = e.target && e.target.closest && e.target.closest('[data-remove-selected-person]');
    if (!btn) return;
    const id = Number(btn.getAttribute('data-remove-selected-person'));
    if (!Number.isFinite(id)) return;
    removeSelectedPerson(id);
  });

  // Handle clear results button
  document.addEventListener('click', function (e) {
    const clearBtn = e.target && e.target.closest && e.target.closest('#clear-results-btn');
    if (!clearBtn) return;
    
    // Remove all selected people
    const container = document.getElementById('selected-people');
    const tiles = container.querySelectorAll('.collab-person-tile');
    tiles.forEach(tile => {
      tile.remove();
    });
    
    // Add back the "no people selected" message
    if (!container.querySelector('.collab-person-tile')) {
      const empty = document.createElement('p');
      empty.id = 'selected-empty';
      empty.className = 'muted';
      empty.textContent = 'No people selected yet.';
      container.appendChild(empty);
    }
    
    // Hide results
    checkAndClearResultsIfNeeded();
    
    // Scroll to search
    const searchInput = document.getElementById('person-query');
    if (searchInput) {
      searchInput.scrollIntoView({ behavior: 'smooth' });
      searchInput.focus();
    }
  });

  // Handle frequent collaborator pairs (divs with role="button")
  function handleFrequentPairClick(elem) {
    const pairIdsStr = elem.getAttribute('data-pair-ids') || '';
    const pairNamesStr = elem.getAttribute('data-pair-names') || '';
    const pairProfilesStr = elem.getAttribute('data-pair-profiles') || '';
    const pairDepartmentsStr = elem.getAttribute('data-pair-departments') || '';
    
    const pairIds = pairIdsStr.split(',').map(id => Number(id.trim())).filter(Number.isFinite);
    const pairNames = pairNamesStr.split('|').map(n => n.trim());
    const pairProfiles = pairProfilesStr.split('|').map(p => p.trim());
    const pairDepartments = pairDepartmentsStr.split('|').map(d => d.trim());
    
    // Add both people to selected
    for (let i = 0; i < pairIds.length; i++) {
      if (Number.isFinite(pairIds[i])) {
        addSelectedPerson(
          pairIds[i],
          pairNames[i] || '',
          pairProfiles[i] || '',
          pairDepartments[i] || 'Unknown'
        );
      }
    }
    
    // Scroll to form and submit - find the collaboration form specifically
    setTimeout(() => {
      const form = document.querySelector('.collab-section form') || document.querySelector('main form[method="post"]');
      if (form) {
        form.scrollIntoView({ behavior: 'smooth' });
        form.submit();
      }
    }, 100);
  }
  
  document.addEventListener('click', function (e) {
    const div = e.target && e.target.closest && e.target.closest('.collab-frequent-pair');
    if (!div) return;
    handleFrequentPairClick(div);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const div = e.target && e.target.closest && e.target.closest('[role="button"].collab-frequent-pair');
    if (!div) return;
    e.preventDefault();
    handleFrequentPairClick(div);
  });

  const frequentList = document.querySelector('.collab-frequent-list');
  const frequentSortToggle = document.querySelector('[data-frequent-sort-toggle]');

  if (frequentList && frequentSortToggle) {
    const originalItems = Array.from(frequentList.querySelectorAll('.collab-frequent-pair'));
    let currentSort = frequentSortToggle.dataset.currentSort || 'random';

    function updateFrequentSortButton() {
      const label = frequentSortToggle.querySelector('[data-frequent-sort-label]');
      if (label) {
        label.textContent = currentSort === 'most' ? 'Random' : 'Most films';
      }
      frequentSortToggle.setAttribute('aria-pressed', currentSort === 'most' ? 'true' : 'false');
    }

    function renderFrequentOrder(mode) {
      const items = originalItems.slice();

      if (mode === 'most') {
        items.sort(function (a, b) {
          const countA = Number(a.dataset.sharedCount || '0');
          const countB = Number(b.dataset.sharedCount || '0');
          if (countA !== countB) {
            return countB - countA;
          }

          const orderA = Number(a.dataset.originalOrder || '0');
          const orderB = Number(b.dataset.originalOrder || '0');
          return orderA - orderB;
        });
      } else {
        items.sort(function (a, b) {
          const orderA = Number(a.dataset.originalOrder || '0');
          const orderB = Number(b.dataset.originalOrder || '0');
          return orderA - orderB;
        });
      }

      items.forEach(function (item) {
        frequentList.appendChild(item);
      });

      currentSort = mode;
      updateFrequentSortButton();
    }

    frequentSortToggle.addEventListener('click', function () {
      renderFrequentOrder(currentSort === 'most' ? 'random' : 'most');
    });

    updateFrequentSortButton();
  }
})();
