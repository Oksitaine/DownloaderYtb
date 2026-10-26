function send(message) {
  return new Promise((resolve) => chrome.runtime.sendMessage(message, (r) => resolve(r || { ok: false })));
}

const STATE_LABELS = {
  queued: 'En attente',
  preparing: 'Préparation',
  downloading: 'Téléchargement',
  processing: 'Finalisation',
  done: 'Terminé',
  error: 'Erreur',
};

async function refreshHealth() {
  const dot = document.getElementById('dot');
  const text = document.getElementById('status-text');
  const hint = document.getElementById('offline-hint');
  const res = await send({ type: 'health' });
  if (res.ok && res.host && res.host.ytdlp_version && res.host.ffmpeg) {
    const host = res.host;
    const notes = [];
    if (host.version !== chrome.runtime.getManifest().version) {
      notes.push('Le programme local n\'est pas à jour : relance host/installer.command, puis recharge l\'extension.');
    }
    if (!host.ytdlp_managed && host.ytdlp_age_days > 30) {
      notes.push('yt-dlp date de ' + host.ytdlp_age_days + ' jours et YouTube bloque souvent les anciennes versions : '
        + 'relance host/installer.command pour une version mise à jour automatiquement.');
    }
    if (!host.js_runtime) notes.push('Aucun moteur JavaScript : brew install deno (YouTube en a besoin).');
    if (host.careful_until) {
      const until = new Date(host.careful_until * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      notes.push('YouTube a bloqué récemment : téléchargements espacés jusqu\'à ' + until + '.');
    }
    dot.className = notes.length ? 'dot warn' : 'dot ok';
    text.textContent = 'Prêt (yt-dlp ' + host.ytdlp_version + (host.ytdlp_managed ? ', mis à jour automatiquement)' : ')');
    hint.textContent = notes.join(' ');
    hint.hidden = !notes.length;
  } else if (res.ok) {
    dot.className = 'dot err';
    text.textContent = res.host && !res.host.ytdlp_version ? 'yt-dlp introuvable' : 'ffmpeg introuvable';
    hint.textContent = 'Installe les outils : brew install ffmpeg deno python, puis relance host/installer.command';
    hint.hidden = false;
  } else {
    dot.className = 'dot err';
    text.textContent = 'Hôte natif indisponible';
    hint.textContent = res.error || 'Lance host/installer.command une fois, puis recharge l\'extension.';
    hint.hidden = false;
  }
}

async function refreshJobs() {
  const list = document.getElementById('jobs');
  const res = await send({ type: 'list-jobs' });
  list.replaceChildren();
  if (!res.ok || !res.jobs || !res.jobs.length) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = 'Aucun téléchargement pour le moment.';
    list.appendChild(li);
    return;
  }
  for (const job of res.jobs.slice().reverse().slice(0, 10)) {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = job.title || job.filename || job.url;
    name.title = job.error || job.filename || job.url;
    const state = document.createElement('span');
    state.className = 'state ' + job.status;
    state.textContent =
      job.status === 'downloading' && typeof job.progress === 'number'
        ? Math.round(job.progress) + ' %'
        : (job.status === 'preparing' && job.note) || STATE_LABELS[job.status] || job.status;
    li.appendChild(name);
    li.appendChild(state);
    if (job.status === 'done' && job.filepath) {
      const btn = document.createElement('button');
      btn.className = 'reveal';
      btn.textContent = 'Finder';
      btn.title = 'Afficher dans le Finder';
      btn.addEventListener('click', () => send({ type: 'reveal', jobId: job.id }));
      li.appendChild(btn);
    }
    list.appendChild(li);
  }
}

async function init() {
  const format = document.getElementById('format');
  const quality = document.getElementById('quality');
  const cookies = document.getElementById('cookies');
  const qualityBlock = document.getElementById('quality-block');
  const res = await send({ type: 'get-settings' });
  if (res.ok) {
    format.value = res.settings.format || 'mp4';
    quality.value = res.settings.maxHeight || 'best';
    cookies.value = res.settings.cookiesMode || 'auto';
  }
  const syncQualityVisibility = () => { qualityBlock.hidden = format.value === 'mp3'; };
  syncQualityVisibility();
  format.addEventListener('change', () => {
    send({ type: 'set-settings', settings: { format: format.value } });
    syncQualityVisibility();
  });
  quality.addEventListener('change', () => send({ type: 'set-settings', settings: { maxHeight: quality.value } }));
  cookies.addEventListener('change', () => send({ type: 'set-settings', settings: { cookiesMode: cookies.value } }));

  const folder = document.getElementById('folder');
  if (res.ok) folder.value = res.settings.downloadDir || '';
  let saveTimer = null;
  folder.addEventListener('input', () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => send({ type: 'set-settings', settings: { downloadDir: folder.value.trim() } }), 400);
  });
  document.getElementById('choose-folder').addEventListener('click', async () => {
    const chosen = await send({ type: 'choose-folder' });
    if (chosen.ok && chosen.path) folder.value = chosen.path;
    else if (!chosen.ok) alert(chosen.error || 'Sélecteur indisponible');
  });

  await refreshHealth();
  await refreshJobs();
  setInterval(refreshJobs, 1500);
}

init();
