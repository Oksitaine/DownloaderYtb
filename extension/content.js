// Content script : injecte un bouton "Télécharger" à droite de J'aime / Je n'aime pas
// sur les pages /watch de YouTube, et pilote son état pendant le téléchargement.

(() => {
  // Le script peut être injecté deux fois (déclaration du manifeste + injection de secours
  // par le service worker) : on ne s'exécute qu'une seule fois par page.
  if (window.__ytbDlInjected) return;
  window.__ytbDlInjected = true;

  const BUTTON_ID = 'ytb-dl-button';
  const WRAPPER_ID = 'ytb-dl-wrapper';
  const POLL_INTERVAL_MS = 1000;

  // Chemins SVG (icônes Material, même famille que celles de YouTube).
  // Les icônes sont construites avec createElementNS : YouTube applique la politique
  // "Trusted Types", qui interdit l'injection de HTML sous forme de chaîne sur toute la page.
  const ICON_DOWNLOAD = 'M17 18v1H6v-1h11zm-.5-6.6-.7-.7-3.8 3.7V4h-1v10.4l-3.8-3.8-.7.7 5 5 5-4.9z';
  const ICON_DONE = 'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2z';
  const ICON_ERROR = 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z';

  function makeSvg(pathData) {
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '24');
    svg.setAttribute('height', '24');
    svg.setAttribute('focusable', 'false');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('fill', 'currentColor');
    path.setAttribute('d', pathData);
    svg.appendChild(path);
    return svg;
  }

  // videoId -> { jobId, timer, lastStatus, finished }
  const activeJobs = new Map();

  // Format choisi dans le popup ('mp4' | 'mp3'), pour le libellé du bouton.
  let format = 'mp4';

  function idleLabel() {
    // Les abonnés Premium ont déjà un bouton "Télécharger" (hors ligne) dans la même
    // rangée : on précise alors le format pour distinguer les deux boutons.
    const container = document.querySelector('#top-level-buttons-computed');
    const nativeDownload = container && [...container.querySelectorAll('button')].some(
      (b) => b.id !== BUTTON_ID && /^(télécharger|download)$/i.test(b.textContent.trim())
    );
    if (!nativeDownload) return 'Télécharger';
    return format === 'mp3' ? 'Télécharger MP3' : 'Télécharger MP4';
  }

  function currentVideoId() {
    if (location.pathname !== '/watch') return null;
    return new URLSearchParams(location.search).get('v');
  }

  function currentTitle() {
    const heading = document.querySelector('ytd-watch-metadata h1, #title h1, h1.ytd-watch-metadata');
    const text = heading && heading.textContent.trim();
    if (text) return text;
    return document.title.replace(/\s*-\s*YouTube\s*$/i, '').trim();
  }


  function canonicalUrl(videoId) {
    return 'https://www.youtube.com/watch?v=' + encodeURIComponent(videoId);
  }

  function findAnchor() {
    // Conteneur des actions sous la vidéo (J'aime, Partager, ...)
    const container = document.querySelector(
      'ytd-watch-metadata #top-level-buttons-computed, ytd-watch-metadata #actions-inner #menu #top-level-buttons-computed, #top-level-buttons-computed'
    );
    if (!container) return null;

    // Le bloc J'aime / Je n'aime pas (nouvelle et ancienne version du DOM YouTube)
    const likeDislike = container.querySelector(
      'segmented-like-dislike-button-view-model, ytd-segmented-like-dislike-button-renderer, like-button-view-model'
    );
    return { container, likeDislike };
  }

  // Classes des boutons YouTube actuels (Partager, ...) et de la génération précédente.
  // On copie de préférence les classes du bouton voisin réel, pour suivre YouTube
  // automatiquement ; ces listes ne servent que si aucun voisin n'est trouvé.
  const FALLBACK_CLASSES = {
    button: [
      'ytSpecButtonShapeNextHost', 'ytSpecButtonShapeNextTonal', 'ytSpecButtonShapeNextMono',
      'ytSpecButtonShapeNextSizeM', 'ytSpecButtonShapeNextIconLeading',
      'yt-spec-button-shape-next', 'yt-spec-button-shape-next--tonal', 'yt-spec-button-shape-next--mono',
      'yt-spec-button-shape-next--size-m', 'yt-spec-button-shape-next--icon-leading',
    ],
    icon: ['ytSpecButtonShapeNextIcon', 'yt-spec-button-shape-next__icon'],
    text: ['ytSpecButtonShapeNextButtonTextContent', 'yt-spec-button-shape-next__button-text-content'],
  };

  function referenceButton(container) {
    const buttons = container.querySelectorAll('yt-button-view-model button, button-view-model button');
    for (const candidate of buttons) {
      if (candidate.id === BUTTON_ID) continue;
      if (candidate.closest('#' + WRAPPER_ID)) continue;
      // On ignore les boutons J'aime / Je n'aime pas (formes segmentées) : Partager est idéal.
      if (/segmented/i.test(candidate.className)) continue;
      if (candidate.className) return candidate;
    }
    return null;
  }

  function mirroredClasses(reference, fallback, filter) {
    if (!reference || typeof reference.className !== 'string' || !reference.className.trim()) return fallback;
    const classes = reference.className.split(/\s+/).filter((c) => c && !filter.test(c));
    return classes.length ? classes : fallback;
  }

  function buildButton(container) {
    const ref = referenceButton(container);
    const refIcon = ref && ref.querySelector('div');
    const refText = ref && [...ref.querySelectorAll('div')].find((d) => d.textContent.trim() && !d.querySelector('svg'));

    const wrapper = document.createElement('div');
    wrapper.id = WRAPPER_ID;

    const button = document.createElement('button');
    button.id = BUTTON_ID;
    button.type = 'button';
    button.setAttribute('aria-label', 'Télécharger la vidéo');
    button.title = 'Télécharger la vidéo';
    button.classList.add(...mirroredClasses(ref, FALLBACK_CLASSES.button, /segmented|disabled|toggled|selected/i));

    const icon = document.createElement('div');
    icon.classList.add('ytb-dl-icon', ...mirroredClasses(refIcon, FALLBACK_CLASSES.icon, /^$/));
    icon.appendChild(makeSvg(ICON_DOWNLOAD));

    const text = document.createElement('div');
    text.classList.add('ytb-dl-text', ...mirroredClasses(refText, FALLBACK_CLASSES.text, /^$/));
    text.textContent = idleLabel();

    button.appendChild(icon);
    button.appendChild(text);
    button.addEventListener('click', onClick);

    wrapper.appendChild(button);
    return wrapper;
  }

  // Si les classes YouTube n'ont produit aucun style (nouvelle refonte du DOM),
  // on active notre feuille de style de secours qui imite la pilule tonal.
  function activateFallbackIfNeeded(button) {
    const cs = getComputedStyle(button);
    const styled = cs.borderRadius !== '0px' && cs.backgroundColor !== 'rgba(0, 0, 0, 0)';
    button.classList.toggle('ytb-dl-fallback', !styled);
  }

  function setState(button, state, label, icon) {
    button.dataset.state = state;
    button.querySelector('.ytb-dl-text').textContent = label;
    if (icon) {
      button.querySelector('.ytb-dl-icon').replaceChildren(makeSvg(icon));
    }
    button.disabled = state === 'working';
  }

  function resetButton(button) {
    setState(button, 'idle', idleLabel(), ICON_DOWNLOAD);
  }

  function ensureButton() {
    const videoId = currentVideoId();
    const existing = document.getElementById(WRAPPER_ID);

    if (!videoId) {
      if (existing) existing.remove();
      return;
    }

    const anchor = findAnchor();
    if (!anchor) return;

    if (existing && existing.isConnected && existing.parentElement === anchor.container) {
      syncButtonWithJob(existing.querySelector('button'), videoId);
      return;
    }
    if (existing) existing.remove();

    const wrapper = buildButton(anchor.container);
    if (anchor.likeDislike && anchor.likeDislike.parentElement === anchor.container) {
      anchor.likeDislike.insertAdjacentElement('afterend', wrapper);
    } else {
      anchor.container.prepend(wrapper);
    }
    const button = wrapper.querySelector('button');
    activateFallbackIfNeeded(button);
    syncButtonWithJob(button, videoId);
  }

  function syncButtonWithJob(button, videoId) {
    const job = activeJobs.get(videoId);
    if (job && job.lastStatus) {
      applyStatus(button, job.lastStatus);
    } else if (button.dataset.state !== 'idle') {
      resetButton(button);
    } else {
      const label = idleLabel();
      const text = button.querySelector('.ytb-dl-text');
      if (text.textContent !== label) text.textContent = label;
    }
  }

  function showToast(message, isError) {
    let toast = document.getElementById('ytb-dl-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'ytb-dl-toast';
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.toggle('ytb-dl-toast--error', !!isError);
    toast.classList.add('ytb-dl-toast--visible');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove('ytb-dl-toast--visible'), 6000);
  }

  function send(message) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(message, (response) => {
          if (chrome.runtime.lastError) {
            resolve({ ok: false, error: chrome.runtime.lastError.message });
          } else {
            resolve(response || { ok: false, error: 'Pas de réponse' });
          }
        });
      } catch (e) {
        resolve({ ok: false, error: String(e) });
      }
    });
  }

  async function onClick(event) {
    event.preventDefault();
    event.stopPropagation();

    const button = event.currentTarget;
    const videoId = currentVideoId();
    if (!videoId) return;
    const previous = activeJobs.get(videoId);
    if (previous && !previous.finished) return; // téléchargement déjà en cours
    if (previous) {
      clearTimeout(previous.resetTimer);
      activeJobs.delete(videoId);
    }

    setState(button, 'working', 'Préparation…', ICON_DOWNLOAD);

    const response = await send({
      type: 'start-download',
      url: canonicalUrl(videoId),
      videoId,
      title: currentTitle(),
    });
    if (!response.ok) {
      setState(button, 'error', 'Erreur', ICON_ERROR);
      showToast('Téléchargement impossible : ' + (response.error || 'erreur inconnue'), true);
      setTimeout(() => resetButton(button), 4000);
      return;
    }

    const job = { jobId: response.jobId, lastStatus: null, timer: null, finished: false, resetTimer: null, polling: false };
    activeJobs.set(videoId, job);
    job.timer = setInterval(() => pollJob(videoId), POLL_INTERVAL_MS);
    pollJob(videoId);
  }

  async function pollJob(videoId) {
    const entry = activeJobs.get(videoId);
    if (!entry || entry.finished || entry.polling) return;

    entry.polling = true;
    let response;
    try {
      response = await send({ type: 'job-status', jobId: entry.jobId });
    } finally {
      entry.polling = false;
    }
    if (!activeJobs.get(videoId) || entry.finished) return;

    if (!response.ok) {
      // Le service worker a redémarré et perdu la tâche : l'hôte natif a été coupé.
      if (response.notFound) finishJob(videoId, { status: 'error', error: 'Téléchargement interrompu (extension redémarrée)' });
      return;
    }
    entry.lastStatus = response.job;

    const button = document.getElementById(BUTTON_ID);
    if (button && currentVideoId() === videoId) applyStatus(button, response.job);

    if (response.job.status === 'done' || response.job.status === 'error') {
      finishJob(videoId, response.job);
    }
  }

  function finishJob(videoId, status) {
    const job = activeJobs.get(videoId) || { timer: null };
    if (job.timer) clearInterval(job.timer);
    // On garde l'état final ("Terminé" / "Erreur") affiché quelques secondes, même si
    // YouTube re-rend la rangée de boutons entre-temps (syncButtonWithJob le réapplique).
    job.finished = true;
    job.lastStatus = status;
    activeJobs.set(videoId, job);
    job.resetTimer = setTimeout(() => {
      if (activeJobs.get(videoId) === job) activeJobs.delete(videoId);
      const button = document.getElementById(BUTTON_ID);
      if (button && currentVideoId() === videoId) resetButton(button);
    }, 5000);

    const button = document.getElementById(BUTTON_ID);
    if (button && currentVideoId() === videoId) applyStatus(button, status);

    if (status.status === 'done') {
      showToast('Téléchargé : ' + (status.filename || 'vidéo') + (status.folder ? ' dans ' + status.folder : ''));
    } else if (status.status === 'error') {
      showToast('Échec du téléchargement : ' + (status.error || 'erreur inconnue'), true);
    }
  }

  function applyStatus(button, job) {
    switch (job.status) {
      case 'queued':
        // D'autres téléchargements sont en cours : celui-ci attend son tour.
        setState(button, 'working', 'En attente…', ICON_DOWNLOAD);
        break;
      case 'preparing':
        // Note courte de l'hôte : « Mise à jour… », « Pause 20 s… », « Essai 2… ».
        setState(button, 'working', job.note || 'Préparation…', ICON_DOWNLOAD);
        break;
      case 'downloading': {
        const pct = typeof job.progress === 'number' ? Math.round(job.progress) : null;
        setState(button, 'working', pct === null ? 'Téléchargement…' : pct + ' %', ICON_DOWNLOAD);
        if (pct !== null) button.style.setProperty('--ytb-dl-progress', pct + '%');
        break;
      }
      case 'processing':
        setState(button, 'working', 'Finalisation…', ICON_DOWNLOAD);
        button.style.setProperty('--ytb-dl-progress', '100%');
        break;
      case 'done':
        setState(button, 'done', 'Terminé', ICON_DONE);
        button.style.removeProperty('--ytb-dl-progress');
        break;
      case 'error':
        setState(button, 'error', 'Erreur', ICON_ERROR);
        button.style.removeProperty('--ytb-dl-progress');
        break;
      default:
        break;
    }
  }

  // --- Détection des changements de page (YouTube est une SPA) ---
  // setTimeout plutôt que requestAnimationFrame : rAF ne s'exécute pas dans un onglet
  // en arrière-plan, le bouton doit pourtant être prêt quand l'onglet devient visible.
  let scheduled = false;
  function scheduleEnsure() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => {
      scheduled = false;
      ensureButton();
    }, 50);
  }

  document.addEventListener('yt-navigate-finish', scheduleEnsure);
  document.addEventListener('yt-page-data-updated', scheduleEnsure);
  window.addEventListener('popstate', scheduleEnsure);

  const observer = new MutationObserver(scheduleEnsure);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  send({ type: 'get-settings' }).then((res) => {
    if (res.ok && res.settings && res.settings.format) {
      format = res.settings.format;
      scheduleEnsure();
    }
  });
  try {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === 'sync' && changes.format) {
        format = changes.format.newValue || 'mp4';
        scheduleEnsure();
      }
    });
  } catch (_) {
    // chrome.storage indisponible (ne devrait pas arriver dans l'extension)
  }

  // Permet au service worker de vérifier que le script est bien présent dans l'onglet.
  try {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message && message.type === 'ping-content') {
        sendResponse({ alive: true });
        scheduleEnsure();
      }
    });
  } catch (_) {
    // chrome.runtime indisponible
  }

  scheduleEnsure();
})();
