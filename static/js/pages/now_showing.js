(function () {
  const form = document.querySelector('[data-now-showing-refresh]');
  if (!form) return;

  const button = form.querySelector('[data-now-showing-refresh-button]');
  const status = form.parentElement.querySelector('[data-now-showing-refresh-status]');
  const csrf = form.querySelector('input[name="csrfmiddlewaretoken"]');

  function showStatus(message, isError) {
    status.hidden = false;
    status.textContent = message;
    status.classList.toggle('is-error', !!isError);
  }

  function poll(progressUrl) {
    fetch(progressUrl, { credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) throw new Error('Could not read refresh status.');
        return response.json();
      })
      .then(function (job) {
        if (job.status === 'succeeded') {
          showStatus('Updated. Reloading…', false);
          window.setTimeout(function () { window.location.reload(); }, 400);
          return;
        }
        if (job.status === 'failed') {
          button.disabled = false;
          showStatus(job.error || 'Refresh failed.', true);
          return;
        }
        showStatus('Fetching movies and posters…', false);
        window.setTimeout(function () { poll(progressUrl); }, 2000);
      })
      .catch(function (error) {
        button.disabled = false;
        showStatus(error.message || 'Refresh status unavailable.', true);
      });
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    button.disabled = true;
    showStatus('Starting refresh…', false);
    fetch(form.action, {
      method: 'POST',
      body: new FormData(form),
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrf ? csrf.value : '', 'X-Requested-With': 'XMLHttpRequest' },
    })
      .then(function (response) {
        if (!response.ok) throw new Error('Could not start refresh.');
        return response.json();
      })
      .then(function (job) { poll(job.progress_url); })
      .catch(function (error) {
        button.disabled = false;
        showStatus(error.message || 'Refresh failed.', true);
      });
  });
})();
