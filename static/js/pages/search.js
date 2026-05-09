(function () {
  const tabWrap = document.getElementById('searchTabs');
  if (tabWrap) {
    const tabs = Array.from(tabWrap.querySelectorAll('.search-tab'));
    const panels = Array.from(document.querySelectorAll('.search-panel'));

    function activateTab(tabName) {
      for (const tab of tabs) {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
      }
      for (const panel of panels) {
        panel.classList.toggle('active', panel.dataset.panel === tabName);
      }
    }

    function panelHasResults(panel) {
      return !!(panel && panel.querySelector('.search-result-card'));
    }

    const initialPanel = panels.find(panelHasResults);
    if (initialPanel && initialPanel.dataset.panel) {
      activateTab(initialPanel.dataset.panel);
    }

    tabWrap.addEventListener('click', function (e) {
      const tab = e.target && e.target.closest && e.target.closest('.search-tab');
      if (!tab) {
        return;
      }
      activateTab(tab.dataset.tab || 'people');
    });
  }

  const input = document.getElementById('search-q');
  const wrap = document.getElementById('search-suggestions');
  const body = document.getElementById('search-suggestions-body');

  if (!input || !wrap || !body) {
    return;
  }

  const SUGGEST_URL = input.dataset.suggestUrl;
  const PLACEHOLDER_PERSON = input.dataset.placeholderPerson;
  const PLACEHOLDER_COMPANY = input.dataset.placeholderCompany;
  const PLACEHOLDER_MOVIE = input.dataset.placeholderMovie;

  if (!SUGGEST_URL) {
    return;
  }

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>'"]/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[c];
    });
  }

  function hide() {
    wrap.style.display = 'none';
    body.innerHTML = '';
  }

  function escapeAttr(s) {
    return escapeHtml(s);
  }

  function yearFromDate(value) {
    const text = String(value || '').trim();
    if (text.length >= 4) {
      return text.slice(0, 4);
    }
    return 'TBA';
  }

  function personRowHtml(imgUrl, name, id, url) {
    return (
      '<a class="search-suggestion-item no-decoration" href="' + escapeAttr(url) + '">' +
        '<img class="search-suggestion-avatar search-suggestion-avatar-person" alt="" src="' + escapeAttr(imgUrl) + '" />' +
        '<div class="search-suggestion-text">' +
          '<div class="search-suggestion-title">' + escapeHtml(name) + '</div>' +
          '<div class="muted search-suggestion-meta">#' + escapeHtml(id) + '</div>' +
        '</div>' +
      '</a>'
    );
  }

  function companyRowHtml(imgUrl, name, id, url) {
    return (
      '<a class="search-suggestion-item no-decoration" href="' + escapeAttr(url) + '">' +
        '<div class="search-suggestion-avatar search-suggestion-avatar-company-wrap">' +
          '<img class="search-suggestion-avatar-company" alt="" src="' + escapeAttr(imgUrl) + '" />' +
        '</div>' +
        '<div class="search-suggestion-text">' +
          '<div class="search-suggestion-title">' + escapeHtml(name) + '</div>' +
          '<div class="muted search-suggestion-meta">#' + escapeHtml(id) + '</div>' +
        '</div>' +
      '</a>'
    );
  }

  function movieRowHtml(imgUrl, title, releaseDate, director, url) {
    return (
      '<a class="search-suggestion-item no-decoration" href="' + escapeAttr(url) + '">' +
        '<img class="search-suggestion-poster" alt="" src="' + escapeAttr(imgUrl) + '" />' +
        '<div class="search-suggestion-text">' +
          '<div class="search-suggestion-title">' + escapeHtml(title) + ' (' + escapeHtml(yearFromDate(releaseDate)) + ')' + '</div>' +
          '<div class="muted search-suggestion-meta">Directed by: ' + escapeHtml(director || 'Unknown') + '</div>' +
        '</div>' +
      '</a>'
    );
  }

  function categoryHtml(title, itemsHtml) {
    if (!itemsHtml.length) {
      return '';
    }
    return (
      '<section class="search-suggestion-category">' +
        '<div class="muted search-suggestion-section">' + escapeHtml(title) + '</div>' +
        '<div class="search-suggestion-list">' + itemsHtml.join('') + '</div>' +
      '</section>'
    );
  }

  function render(data) {
    const people = (data && data.people) || [];
    const companies = (data && data.companies) || [];
    const movies = (data && data.movies) || [];

    if (!people.length && !companies.length && !movies.length) {
      hide();
      return;
    }

    const parts = [];

    const peopleItems = [];
    for (const p of people) {
      const img = p.profile_path ? ('https://image.tmdb.org/t/p/w185' + p.profile_path) : (PLACEHOLDER_PERSON || '');
      peopleItems.push(personRowHtml(img, p.name || '', p.id, p.url || '#'));
    }
    parts.push(categoryHtml('People', peopleItems));

    const companyItems = [];
    for (const c of companies) {
      const img = c.logo_path ? ('https://image.tmdb.org/t/p/w185' + c.logo_path) : (PLACEHOLDER_COMPANY || '');
      companyItems.push(companyRowHtml(img, c.name || '', c.id, c.url || '#'));
    }
    parts.push(categoryHtml('Companies', companyItems));

    const movieItems = [];
    for (const m of movies) {
      const img = m.poster_path ? ('https://image.tmdb.org/t/p/w342' + m.poster_path) : (PLACEHOLDER_MOVIE || '');
      movieItems.push(movieRowHtml(img, m.title || '', m.release_date || '', m.director || '', m.url || '#'));
    }
    parts.push(categoryHtml('Movies', movieItems));

    body.innerHTML = parts.join('');
    wrap.style.display = 'block';
  }

  let debounce = null;
  let lastQuery = '';

  async function fetchSuggest(q) {
    const url = SUGGEST_URL + '?q=' + encodeURIComponent(q) + '&limit=5';
    const resp = await fetch(url, { headers: { 'Accept': 'application/json' } });
    return await resp.json();
  }

  input.addEventListener('input', function () {
    const q = (input.value || '').trim();
    lastQuery = q;

    if (debounce) {
      clearTimeout(debounce);
    }
    if (q.length < 2) {
      hide();
      return;
    }

    debounce = setTimeout(async function () {
      try {
        const data = await fetchSuggest(q);
        if (lastQuery !== q) {
          return;
        }
        render(data);
      } catch (e) {
        hide();
      }
    }, 250);
  });

  input.addEventListener('blur', function () {
    // Small delay so clicks on suggestions still work.
    setTimeout(hide, 200);
  });
})();
