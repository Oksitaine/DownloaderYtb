// Service worker : pour chaque téléchargement, Chrome lance l'hôte natif (host/ytb_dl_host.py)
// via le Native Messaging. L'hôte appelle yt-dlp et renvoie la progression ; il se termine
// tout seul à la fin. Rien ne tourne en arrière-plan entre deux téléchargements.
//
// Le script de contenu interroge "job-status" chaque seconde : ces messages maintiennent le
// service worker actif tant qu'un téléchargement est en cours (sinon Chrome l'arrêterait et
// couperait la connexion avec l'hôte).

const NATIVE_HOST = 'com.oksitaine.ytb_dl';
// format : 'mp4' | 'mp3' ; maxHeight : 'best' | '2160' | ... | '240' ; downloadDir : '' = ~/Downloads/YouTube
// cookiesMode : session YouTube transmise à yt-dlp, 'auto' (en secours si YouTube bloque) | 'always' | 'never'
const DEFAULT_SETTINGS = { format: 'mp4', maxHeight: 'best', downloadDir: '', cookiesMode: 'auto' };
const MAX_JOBS_KEPT = 30;
// Au-delà, les téléchargements attendent leur tour : trop de requêtes simultanées font bloquer
// l'adresse IP par YouTube. L'hôte espace aussi les démarrages de yt-dlp.
const MAX_PARALLEL = 2;

// jobId -> { id, url, title, status, progress, note, filename, filepath, error, port, createdAt }
// status : queued (attend son tour) | preparing | downloading | processing | done | error
const jobs = new Map();
const jobsOrder = [];
const waiting = [];
let running = 0;
let nextJobId = 1;

async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULT_SETTINGS);
  return { ...DEFAULT_SETTINGS, ...stored };
}

function publicJob(job) {
  const { port, ...rest } = job;
  return rest;
}

function rememberJob(job) {
  jobs.set(job.id, job);
  jobsOrder.push(job.id);
  while (jobsOrder.length > MAX_JOBS_KEPT) {
    const oldId = jobsOrder[0];
    const old = jobs.get(oldId);
    if (old && (old.status === 'done' || old.status === 'error')) {
      jobsOrder.shift();
      jobs.delete(oldId);
    } else {
      break;
    }
  }
}

function hostErrorMessage(rawMessage) {
  const text = String(rawMessage || '');
  if (/not found|introuvable|Specified native messaging host/i.test(text)) {
    return 'Hôte natif non installé : lance host/installer.command une fois (voir README).';
  }
  if (/Access to the specified native messaging host is forbidden/i.test(text)) {
    return 'Hôte natif installé pour une autre extension : relance host/installer.command.';
  }
  if (/Native host has exited/i.test(text)) {
    return 'L\'hôte natif s\'est arrêté brutalement (yt-dlp ou ffmpeg manquant ?).';
  }
  return text || 'Erreur de communication avec l\'hôte natif';
}

function connectHost() {
  return chrome.runtime.connectNative(NATIVE_HOST);
}

// Envoie un message unique à l'hôte et attend sa réponse (ping, reveal...).
function askHost(message, timeoutMs = 8000) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        port.disconnect();
      } catch (_) {
        // déjà fermé
      }
      resolve(value);
    };
    const port = connectHost();
    const timer = setTimeout(() => finish({ ok: false, error: 'L\'hôte natif ne répond pas' }), timeoutMs);
    port.onMessage.addListener((response) => finish({ ok: true, response }));
    port.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError;
      finish({ ok: false, error: hostErrorMessage(err && err.message) });
    });
    port.postMessage(message);
  });
}

// Cookies YouTube de ce navigateur, pour que yt-dlp puisse se présenter avec la session de
// l'utilisateur quand YouTube bloque. Ils ne quittent pas l'ordinateur : l'hôte les écrit dans
// un fichier temporaire lisible par l'utilisateur seul, supprimé à la fin du téléchargement.
async function youtubeCookies(mode) {
  if (mode === 'never') return null;
  try {
    const cookies = await chrome.cookies.getAll({ domain: 'youtube.com' });
    return cookies.map(({ domain, hostOnly, path, secure, expirationDate, name, value }) => ({
      domain, hostOnly, path, secure, expirationDate, name, value,
    }));
  } catch (_) {
    return null;
  }
}

// Réglages partagés avec la commande ytb-dl (terminal, IA) : même dossier, format, qualité.
async function syncHostConfig() {
  const s = await getSettings();
  askHost({
    type: 'set-config',
    config: { download_dir: s.downloadDir || '', format: s.format, max_height: s.maxHeight, cookies: s.cookiesMode },
  });
}

async function startJob(message) {
  const settings = await getSettings();
  const job = {
    id: String(nextJobId++),
    url: message.url,
    videoId: message.videoId,
    title: message.title || null,
    format: settings.format,
    maxHeight: settings.maxHeight,
    dir: settings.downloadDir || '',
    cookiesMode: settings.cookiesMode,
    status: 'queued',
    progress: 0,
    note: null,
    filename: null,
    filepath: null,
    error: null,
    createdAt: Date.now(),
    port: null,
  };
  rememberJob(job);
  waiting.push(job);
  pump();
  return { ok: true, jobId: job.id };
}

function pump() {
  while (running < MAX_PARALLEL && waiting.length) {
    runJob(waiting.shift());
  }
}

async function runJob(job) {
  running++;
  job.status = 'preparing';
  let finished = false;
  const finish = (status, error) => {
    if (finished) return;
    finished = true;
    if (status) job.status = status;
    if (error) job.error = error;
    job.note = null;
    if (job.port) {
      try {
        job.port.disconnect();
      } catch (_) {
        // déjà fermé
      }
      job.port = null;
    }
    running--;
    pump();
  };

  const cookies = await youtubeCookies(job.cookiesMode);
  const port = connectHost();
  job.port = port;

  port.onMessage.addListener((msg) => {
    if (!msg || msg.id !== job.id) return;
    switch (msg.type) {
      case 'progress':
        job.status = { processing: 'processing', preparing: 'preparing' }[msg.phase] || 'downloading';
        job.note = msg.note || null;
        if (typeof msg.progress === 'number') job.progress = msg.progress;
        if (msg.title) job.title = msg.title;
        break;
      case 'done':
        job.progress = 100;
        job.filename = msg.filename;
        job.filepath = msg.filepath;
        job.folder = msg.filepath ? msg.filepath.slice(0, msg.filepath.lastIndexOf('/')).replace(/^\/Users\/[^/]+/, '~') : null;
        if (msg.title) job.title = msg.title;
        finish('done');
        break;
      case 'error':
        finish('error', msg.error || 'erreur inconnue');
        break;
      default:
        break;
    }
  });

  // N'est appelé que si l'hôte se ferme de lui-même (pas quand on appelle port.disconnect()).
  port.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError;
    job.port = null;
    if (job.status !== 'done' && job.status !== 'error') finish('error', hostErrorMessage(err && err.message));
    else finish();
  });

  port.postMessage({
    type: 'download',
    id: job.id,
    url: job.url,
    format: job.format,
    maxHeight: job.maxHeight,
    dir: job.dir,
    cookiesMode: job.cookiesMode,
    cookies,
  });
}

async function handleMessage(message) {
  switch (message.type) {
    case 'health': {
      // Le premier lancement de yt-dlp après une mise à jour peut prendre quelques secondes.
      const res = await askHost({ type: 'ping' }, 30000);
      if (!res.ok) return { ok: false, error: res.error };
      return { ok: true, host: res.response };
    }
    case 'start-download':
      return startJob(message);
    case 'job-status': {
      const job = jobs.get(String(message.jobId));
      if (!job) return { ok: false, notFound: true, error: 'Tâche introuvable' };
      return { ok: true, job: publicJob(job) };
    }
    case 'list-jobs':
      return { ok: true, jobs: jobsOrder.map((id) => jobs.get(id)).filter(Boolean).map(publicJob) };
    case 'reveal': {
      const job = jobs.get(String(message.jobId));
      if (!job || !job.filepath) return { ok: false, error: 'Fichier introuvable' };
      return askHost({ type: 'reveal', filepath: job.filepath });
    }
    case 'choose-folder': {
      const settings = await getSettings();
      const res = await askHost({ type: 'choose-folder', current: settings.downloadDir || '' }, 5 * 60 * 1000);
      if (!res.ok) return { ok: false, error: res.error };
      if (res.response.cancelled || !res.response.path) return { ok: true, cancelled: true };
      await chrome.storage.sync.set({ downloadDir: res.response.path });
      syncHostConfig();
      return { ok: true, path: res.response.path };
    }
    case 'get-settings':
      return { ok: true, settings: await getSettings() };
    case 'set-settings':
      await chrome.storage.sync.set(message.settings || {});
      syncHostConfig();
      return { ok: true, settings: await getSettings() };
    default:
      return { ok: false, error: 'Message inconnu : ' + message.type };
  }
}

// ---------------------------------------------------------------------------
// Injection de secours du script de contenu.
// Chrome n'injecte pas les scripts déclarés dans le manifeste dans certains onglets :
// onglets restaurés au démarrage (chargés avant l'extension), pages préchargées, onglets
// déjà ouverts au moment où l'extension est (re)chargée. On vérifie donc nous-mêmes.
// ---------------------------------------------------------------------------

const YOUTUBE_URL = /^https:\/\/www\.youtube\.com\//;

function pingContent(tabId) {
  return new Promise((resolve) => {
    try {
      chrome.tabs.sendMessage(tabId, { type: 'ping-content' }, (response) => {
        // lastError = aucun récepteur dans l'onglet
        resolve(!chrome.runtime.lastError && !!(response && response.alive));
      });
    } catch (_) {
      resolve(false);
    }
  });
}

async function ensureContentScript(tabId, url) {
  if (!url || !YOUTUBE_URL.test(url)) return;
  if (await pingContent(tabId)) return;
  try {
    await chrome.scripting.insertCSS({ target: { tabId }, files: ['content.css'] });
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
  } catch (_) {
    // onglet fermé, page en cours de chargement, ou accès au site refusé : on réessaiera
  }
}

async function ensureAllYouTubeTabs() {
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ url: 'https://www.youtube.com/*' });
  } catch (_) {
    return;
  }
  for (const tab of tabs) {
    if (tab.id !== undefined) ensureContentScript(tab.id, tab.url);
  }
}

chrome.runtime.onInstalled.addListener(ensureAllYouTubeTabs);
chrome.runtime.onStartup.addListener(ensureAllYouTubeTabs);

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' || changeInfo.url) {
    ensureContentScript(tabId, tab && tab.url ? tab.url : changeInfo.url);
  }
});

chrome.tabs.onActivated.addListener(({ tabId }) => {
  chrome.tabs.get(tabId, (tab) => {
    if (!chrome.runtime.lastError && tab) ensureContentScript(tabId, tab.url);
  });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message || {})
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: String(error && error.message ? error.message : error) }));
  return true; // réponse asynchrone
});
