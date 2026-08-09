(function () {
  var viewer = document.querySelector('[data-image-lightbox]');
  if (!viewer) {
    return;
  }

  var imageEl = viewer.querySelector('[data-image-lightbox-image]');
  var statusEl = viewer.querySelector('[data-image-lightbox-status]');
  var captionEl = viewer.querySelector('[data-image-lightbox-caption]');
  var stageEl = viewer.querySelector('[data-image-lightbox-stage]');
  var closeBtns = viewer.querySelectorAll('[data-image-lightbox-close]');
  var fullscreenBtn = viewer.querySelector('[data-image-lightbox-fullscreen]');
  var galleries = Array.prototype.slice.call(document.querySelectorAll('[data-image-lightbox-gallery]'));

  if (!imageEl || !stageEl || !galleries.length) {
    return;
  }

  var state = {
    open: false,
    items: [],
    index: 0,
    lastFocused: null,
    scrollY: 0,
    pointerId: null,
    pointerStartX: 0,
    pointerStartY: 0,
    pointerTracking: false,
    pendingCloseAfterFullscreenExit: false,
  };

  function isFullscreenActive() {
    return document.fullscreenElement === viewer;
  }

  function getFullscreenLabel() {
    return isFullscreenActive() ? 'Exit full screen' : 'View in full screen';
  }

  function updateFullscreenButton() {
    if (!fullscreenBtn) {
      return;
    }

    var label = getFullscreenLabel();
    fullscreenBtn.setAttribute('aria-label', label);
    fullscreenBtn.setAttribute('title', label);
    fullscreenBtn.setAttribute('aria-pressed', isFullscreenActive() ? 'true' : 'false');
  }

  function finishClose() {
    if (!state.open) {
      return;
    }

    state.open = false;
    state.pendingCloseAfterFullscreenExit = false;
    viewer.hidden = true;
    viewer.setAttribute('aria-hidden', 'true');
    imageEl.removeAttribute('src');
    statusEl.hidden = false;
    statusEl.textContent = 'Loading image…';
    unlockScroll();

    if (state.lastFocused && typeof state.lastFocused.focus === 'function') {
      state.lastFocused.focus({ preventScroll: true });
    }
  }

  function syncFullscreenState() {
    viewer.classList.toggle('is-fullscreen', isFullscreenActive());
    updateFullscreenButton();

    if (!isFullscreenActive() && state.pendingCloseAfterFullscreenExit) {
      finishClose();
    }
  }

  function enterFullscreen() {
    if (!viewer.requestFullscreen || isFullscreenActive()) {
      return;
    }

    try {
      var request = viewer.requestFullscreen({ navigationUI: 'hide' });
      if (request && typeof request.catch === 'function') {
        request.catch(function () {
          // Ignore fullscreen failures and keep the lightbox working normally.
        });
      }
    } catch (error) {
      // Ignore fullscreen failures and keep the lightbox working normally.
    }
  }

  function exitFullscreen() {
    if (!document.exitFullscreen || !isFullscreenActive()) {
      return;
    }

    try {
      var request = document.exitFullscreen();
      if (request && typeof request.catch === 'function') {
        request.catch(function () {
          // Ignore fullscreen failures and keep the lightbox working normally.
        });
      }
    } catch (error) {
      // Ignore fullscreen failures and keep the lightbox working normally.
    }
  }

  function toggleFullscreen() {
    if (isFullscreenActive()) {
      exitFullscreen();
      return;
    }

    enterFullscreen();
  }

  function lockScroll() {
    state.scrollY = window.scrollY || document.documentElement.scrollTop || 0;
    document.body.classList.add('image-lightbox-open');
    document.body.style.position = 'fixed';
    document.body.style.top = '-' + state.scrollY + 'px';
    document.body.style.left = '0';
    document.body.style.right = '0';
    document.body.style.width = '100%';
  }

  function unlockScroll() {
    document.body.classList.remove('image-lightbox-open');
    document.body.style.position = '';
    document.body.style.top = '';
    document.body.style.left = '';
    document.body.style.right = '';
    document.body.style.width = '';
    window.scrollTo(0, state.scrollY || 0);
  }

  function normalizeIndex(index) {
    if (!state.items.length) {
      return 0;
    }
    var length = state.items.length;
    return ((index % length) + length) % length;
  }

  function preloadAround(index) {
    if (!state.items.length) {
      return;
    }
    [index - 1, index + 1].forEach(function (candidateIndex) {
      var candidate = state.items[normalizeIndex(candidateIndex)];
      if (!candidate || !candidate.fullUrl) {
        return;
      }
      var preload = new Image();
      preload.src = candidate.fullUrl;
    });
  }

  function render(index) {
    if (!state.items.length) {
      return;
    }
    state.index = normalizeIndex(index);
    var item = state.items[state.index];
    if (!item) {
      return;
    }

    viewer.hidden = false;
    viewer.setAttribute('aria-hidden', 'false');
    imageEl.alt = item.alt || '';
    imageEl.removeAttribute('src');
    imageEl.dataset.loading = '1';
    statusEl.hidden = false;
    statusEl.textContent = 'Loading image…';

    imageEl.onload = function () {
      imageEl.dataset.loading = '0';
      statusEl.hidden = true;
    };

    imageEl.onerror = function () {
      imageEl.dataset.loading = '0';
      statusEl.hidden = false;
      statusEl.textContent = 'Image failed to load.';
    };

    imageEl.src = item.fullUrl || item.src || '';
    preloadAround(state.index);
  }

  function openFromGallery(gallery, button) {
    var buttons = Array.prototype.slice.call(gallery.querySelectorAll('[data-image-lightbox-open]'));
    state.items = buttons.map(function (candidateButton) {
      var image = candidateButton.querySelector('img');
      return {
        button: candidateButton,
        src: candidateButton.dataset.imageLightboxSrc || candidateButton.dataset.fullUrl || (image ? image.currentSrc || image.src : ''),
        fullUrl: candidateButton.dataset.imageLightboxFull || candidateButton.dataset.imageLightboxSrc || (image ? image.currentSrc || image.src : ''),
        alt: candidateButton.dataset.imageLightboxAlt || (image ? image.alt : '') || '',
        caption: candidateButton.dataset.imageLightboxCaption || '',
      };
    });

    state.index = Math.max(0, buttons.indexOf(button));
    state.lastFocused = document.activeElement;
    state.pendingCloseAfterFullscreenExit = false;
    lockScroll();
    state.open = true;
    render(state.index);
  }

  function closeViewer() {
    if (!state.open) {
      return;
    }

    if (isFullscreenActive()) {
      state.pendingCloseAfterFullscreenExit = true;
      exitFullscreen();
      return;
    }

    finishClose();
  }

  function step(delta) {
    if (!state.items.length) {
      return;
    }
    render(state.index + delta);
  }

  galleries.forEach(function (gallery) {
    gallery.addEventListener('click', function (event) {
      var button = event.target && typeof event.target.closest === 'function' ? event.target.closest('[data-image-lightbox-open]') : null;
      if (!button || !gallery.contains(button)) {
        return;
      }
      event.preventDefault();
      openFromGallery(gallery, button);
    });
  });

  closeBtns.forEach(function (button) {
    button.addEventListener('click', closeViewer);
  });

  if (fullscreenBtn) {
    fullscreenBtn.addEventListener('click', function (event) {
      event.preventDefault();
      toggleFullscreen();
    });
  }

  document.addEventListener('fullscreenchange', syncFullscreenState);
  updateFullscreenButton();

  viewer.addEventListener('click', function (event) {
    var target = event.target;
    if (target && typeof target.closest === 'function') {
      if (target.closest('[data-image-lightbox-close]')) {
        closeViewer();
        return;
      }
    }
  });

  document.addEventListener('keydown', function (event) {
    if (!state.open) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      if (isFullscreenActive()) {
        exitFullscreen();
        return;
      }
      closeViewer();
      return;
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      step(-1);
      return;
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      step(1);
    }
  });

  stageEl.addEventListener('pointerdown', function (event) {
    if (!state.open || event.pointerType === 'mouse' && event.button !== 0) {
      return;
    }
    state.pointerId = event.pointerId;
    state.pointerStartX = event.clientX;
    state.pointerStartY = event.clientY;
    state.pointerTracking = true;
    stageEl.setPointerCapture(event.pointerId);
  });

  stageEl.addEventListener('pointerup', function (event) {
    if (!state.open || !state.pointerTracking || state.pointerId !== event.pointerId) {
      return;
    }
    state.pointerTracking = false;
    try {
      stageEl.releasePointerCapture(event.pointerId);
    } catch (error) {
      // ignore release errors
    }
    var deltaX = event.clientX - state.pointerStartX;
    var deltaY = event.clientY - state.pointerStartY;
    if (Math.abs(deltaX) > 48 && Math.abs(deltaX) > Math.abs(deltaY) * 1.15) {
      if (deltaX < 0) {
        step(1);
      } else {
        step(-1);
      }
    }
  });

  stageEl.addEventListener('pointercancel', function () {
    state.pointerTracking = false;
    state.pointerId = null;
  });
})();
