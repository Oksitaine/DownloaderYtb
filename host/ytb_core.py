#!/usr/bin/env python3
"""Cœur commun de YouTube Download Button : lance yt-dlp avec la stratégie anti-blocage la plus
solide possible en local. Utilisé par l'hôte natif (extension Chrome) et par la commande ytb-dl
(terminal, IA, serveur MCP).

Stratégie :
- yt-dlp « géré » : la version officielle en Python pur (zipapp du canal nightly, où les correctifs
  YouTube arrivent en premier) installée par installer.command dans bin/, lancée par un Python 3.10+
  (Homebrew), mise à jour automatiquement une fois par jour et tout de suite quand YouTube casse
  l'extraction. À défaut, le yt-dlp du PATH (Homebrew).
  Pas l'exécutable yt-dlp_macos : il décompresse son Python à chaque lancement, et les fichiers créés
  sous Chrome reçoivent l'attribut de quarantaine, que macOS refuse alors de charger.
- Moteur JavaScript (deno, sinon node, quickjs, bun) pour résoudre les défis de YouTube.
- Vérification anti-robot, limite de débit ou vidéo réservée : nouvel essai avec la session
  YouTube du navigateur, pause, puis mode « prudent » (requêtes espacées) pendant 30 minutes.
- Extraction cassée : mise à jour de yt-dlp, puis d'autres clients YouTube.
- Démarrages de yt-dlp espacés entre tous les programmes (extension, terminal, IA).

Bibliothèque standard uniquement, compatible avec le Python 3.9 de macOS (celui que Chrome lance).
"""

import contextlib
import datetime
import fcntl
import functools
import html
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

VERSION = "4.0.0"  # identique à extension/manifest.json : l'extension repère un hôte pas réinstallé

APP_DIR = Path(os.environ.get("YTB_DL_HOME") or Path.home() / "Library" / "Application Support" / "YouTubeDownloadButton")
MANAGED_YTDLP = APP_DIR / "bin" / "yt-dlp.pyz"
CONFIG_FILE = APP_DIR / "config.json"
STATE_FILE = APP_DIR / "state.json"
LOCK_FILE = APP_DIR / "state.lock"
LOG_FILE = APP_DIR / "logs" / "ytb-dl.log"
TMP_DIR = APP_DIR / "tmp"
DEFAULT_DIR = Path(os.environ.get("YTB_DL_DIR") or Path.home() / "Downloads" / "YouTube")

UPDATE_CHANNEL = "nightly"
UPDATE_EVERY = 24 * 3600          # mise à jour quotidienne du yt-dlp géré
FORCED_UPDATE_GAP = 30 * 60       # au plus une mise à jour forcée par demi-heure
CAREFUL_FOR = 30 * 60             # durée du mode prudent après un blocage
START_GAP = 2.0                   # secondes entre deux lancements de yt-dlp
CAREFUL_START_GAP = (12.0, 25.0)  # idem en mode prudent (tirage aléatoire)
BLOCK_PAUSE = 45                  # pause avant de réessayer après un blocage
MAX_ATTEMPTS = 6
# "default" garde les clients choisis par yt-dlp ; les autres sont ajoutés en secours
# (yt-dlp ignore avec un simple avertissement un nom que sa version ne connaît pas).
FALLBACK_CLIENTS = "default,tv_simply,tv,web_safari,mweb"

ALLOWED_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
MAX_HEIGHTS = {"2160", "1440", "1080", "720", "480", "360", "240", "144"}
DEFAULT_CONFIG = {"download_dir": "", "format": "mp4", "max_height": "best", "cookies": "auto", "browser": "chrome"}
COOKIE_MODES = ("auto", "always", "never")

# Chrome lance l'hôte avec un PATH minimal : on ajoute les emplacements de Homebrew.
for _extra in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")):
    if _extra not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = _extra + os.pathsep + os.environ.get("PATH", "")


class YtdlError(Exception):
    def __init__(self, message, kind="other"):
        super().__init__(message)
        self.kind = kind


class Cancelled(YtdlError):
    def __init__(self):
        super().__init__("Annulé", "cancelled")


class Job:
    """Un appel en cours, annulable depuis un autre fil (Chrome qui coupe, annulation MCP)."""

    def __init__(self):
        self.cancelled = False
        self._proc = None
        self._lock = threading.Lock()

    def attach(self, proc):
        with self._lock:
            self._proc = proc
            if self.cancelled:
                proc.terminate()

    def cancel(self):
        with self._lock:
            self.cancelled = True
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def check(self):
        if self.cancelled:
            raise Cancelled()


def _no_report(**_event):
    pass


# ---------- configuration, état partagé, journal ----------

def load_config():
    config = dict(DEFAULT_CONFIG)
    try:
        stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        config.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG and isinstance(v, str)})
    except (OSError, ValueError):
        pass
    return config


def save_config(updates):
    """Enregistre les réglages (partagés entre l'extension et ytb-dl) ; renvoie la config complète."""
    config = load_config()
    for key, value in updates.items():
        if key not in DEFAULT_CONFIG or value is None:
            continue
        value = str(value).strip()
        if key == "format" and value not in ("mp4", "mp3"):
            raise YtdlError("format : mp4 ou mp3")
        if key == "max_height" and value != "best" and value not in MAX_HEIGHTS:
            raise YtdlError("max_height : best ou " + ", ".join(sorted(MAX_HEIGHTS, key=int)))
        if key == "cookies" and value not in COOKIE_MODES:
            raise YtdlError("cookies : " + ", ".join(COOKIE_MODES))
        config[key] = value
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)
    return config


@contextlib.contextmanager
def shared_state():
    """État partagé entre tous les processus, protégé par un verrou de fichier."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        yield state
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, STATE_FILE)


def read_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def log(*lines):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
            os.replace(LOG_FILE, LOG_FILE.with_suffix(".log.1"))
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(f"{stamp} [{os.getpid()}] {line}\n")
    except OSError:
        pass


# ---------- outils ----------

def find_ytdlp():
    """(chemin, géré) : le yt-dlp géré s'il est installé, sinon celui du PATH."""
    forced = os.environ.get("YTB_DL_YTDLP")
    if forced and os.access(forced, os.X_OK):
        return forced, False
    if MANAGED_YTDLP.is_file() and find_python():
        return str(MANAGED_YTDLP), True
    return shutil.which("yt-dlp"), False


@functools.lru_cache(maxsize=None)
def find_python():
    """Python 3.10+ pour le yt-dlp géré (celui de macOS, 3.9, est trop ancien pour yt-dlp)."""
    if sys.version_info >= (3, 10):
        return sys.executable
    for name in ("python3", "python3.14", "python3.13", "python3.12", "python3.11", "python3.10"):
        path = shutil.which(name)
        if not path:
            continue
        try:
            ok = subprocess.run([path, "-c", "import sys; sys.exit(sys.version_info < (3, 10))"],
                                capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return path
    return None


def ytdlp_cmd(exe):
    """Commande qui lance ce yt-dlp : le zipapp géré passe par Python."""
    return [find_python(), exe] if exe.endswith(".pyz") else [exe]


def js_runtime():
    """Premier moteur JavaScript disponible, dans l'ordre de préférence de yt-dlp."""
    for name, exe in (("deno", "deno"), ("node", "node"), ("quickjs", "qjs"), ("bun", "bun")):
        path = shutil.which(exe)
        if path:
            return name, path
    return None, None


def ytdlp_version(exe):
    try:
        return subprocess.run(ytdlp_cmd(exe) + ["--version"], capture_output=True, text=True, timeout=60).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def version_age_days(version):
    match = re.match(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", version or "")
    if not match:
        return None
    released = datetime.date(*map(int, match.groups()))
    return (datetime.date.today() - released).days


def valid_youtube_url(url):
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_HOSTS


def check_url(url):
    url = str(url or "").strip()
    if not valid_youtube_url(url):
        raise YtdlError("URL YouTube invalide : " + (url or "(vide)"), "invalid")
    if urlparse(url).path.rstrip("/") == "/playlist":
        raise YtdlError("Les playlists ne sont pas prises en charge : donne l'URL d'une vidéo.", "invalid")
    return url


def resolve_dir(value=None):
    text = str(value or "").strip() or load_config()["download_dir"]
    return Path(os.path.expanduser(text)) if text else DEFAULT_DIR


def health():
    exe, managed = find_ytdlp()
    version = ytdlp_version(exe) if exe else None
    runtime, _ = js_runtime()
    state = read_state()
    return {
        "version": VERSION,
        "ytdlp_version": version,
        "ytdlp_path": exe,
        "ytdlp_managed": managed,
        "ytdlp_age_days": version_age_days(version),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "js_runtime": runtime,
        "download_dir": str(resolve_dir()),
        "careful_until": state.get("careful_until", 0) if state.get("careful_until", 0) > time.time() else 0,
        "last_update": state.get("last_update", 0),
        "log_file": str(LOG_FILE),
    }


# ---------- espacement, pauses, mises à jour ----------

def pause(seconds, report, job, note="Pause {} s…"):
    """Attend en donnant des nouvelles toutes les 5 s (garde le service worker de Chrome actif)."""
    end = time.time() + seconds
    last_report = 0.0
    while True:
        left = end - time.time()
        if left <= 0:
            return
        job.check()
        if seconds >= 3 and time.time() - last_report >= 5:  # les courts espacements restent discrets
            report(phase="preparing", note=note.format(math.ceil(left)))
            last_report = time.time()
        time.sleep(min(left, 0.5))


def wait_turn(report, job, quick=False):
    """Espace les lancements de yt-dlp entre tous les processus ; renvoie True en mode prudent.
    quick : nouvel essai immédiat avec la session YouTube, comme le ferait un navigateur."""
    with shared_state() as state:
        now = time.time()
        careful = state.get("careful_until", 0) > now
        gap = random.uniform(*CAREFUL_START_GAP) if careful and not quick else START_GAP
        delay = state.get("last_start", 0) + gap - now
        if delay > 0:
            pause(delay, report, job)
        state["last_start"] = time.time()
    return careful


def mark_careful():
    with shared_state() as state:
        state["careful_until"] = time.time() + CAREFUL_FOR


def update_ytdlp(report=_no_report, job=None, force=False, always=False):
    """Met à jour le yt-dlp géré (une fois par jour, ou forcé quand YouTube casse l'extraction).
    Renvoie True si une mise à jour a été lancée."""
    exe, managed = find_ytdlp()
    if not managed:
        return False
    job = job or Job()
    with shared_state() as state:
        min_gap = 0 if always else FORCED_UPDATE_GAP if force else UPDATE_EVERY
        if time.time() - state.get("last_update", 0) < min_gap:
            return False
        state["last_update"] = time.time()
        report(phase="preparing", note="Mise à jour…")
        before = ytdlp_version(exe)
        proc = subprocess.Popen(ytdlp_cmd(exe) + ["--update-to", UPDATE_CHANNEL], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        job.attach(proc)
        deadline = time.time() + 300
        while proc.poll() is None and time.time() < deadline and not job.cancelled:
            time.sleep(0.5)
            report(phase="preparing", note="Mise à jour…")
        if proc.poll() is None:
            proc.kill()
        output = proc.stdout.read().strip() if proc.stdout else ""
        log(f"mise à jour de yt-dlp ({before}) : {output.replace(chr(10), ' | ')}")
    job.check()
    return True


# ---------- session YouTube (cookies) ----------

class CookieSource:
    """Session YouTube du navigateur : cookies transmis par l'extension, ou lus par yt-dlp
    dans le navigateur (--cookies-from-browser) pour le terminal et les IA."""

    def __init__(self, cookies=None, browser=None):
        self.cookies = cookies or None
        self.browser = browser or None
        self.path = None

    def available(self):
        return bool(self.cookies or self.browser)

    def args(self):
        if self.cookies:
            if not self.path:
                self.path = self._write()
            return ["--cookies", self.path]
        if self.browser:
            return ["--cookies-from-browser", self.browser]
        return []

    def _write(self):
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(TMP_DIR, 0o700)
        fd, path = tempfile.mkstemp(prefix="cookies-", suffix=".txt", dir=TMP_DIR)  # lisible par soi seul
        lines = ["# Netscape HTTP Cookie File"]
        for c in self.cookies:
            fields = [str(c.get(k, "")) for k in ("domain", "path", "name", "value")]
            if not fields[0] or not fields[2] or any(ch in f for f in fields for ch in "\t\r\n"):
                continue
            host_only = bool(c.get("hostOnly"))
            domain = fields[0] if host_only or fields[0].startswith(".") else "." + fields[0]
            lines.append("\t".join([domain, "FALSE" if host_only else "TRUE", fields[1] or "/",
                                    "TRUE" if c.get("secure") else "FALSE", str(int(c.get("expirationDate") or 0)),
                                    fields[2], fields[3]]))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def cleanup(self):
        if self.path:
            with contextlib.suppress(OSError):
                os.remove(self.path)
            self.path = None


# Présents seulement quand un compte est connecté (yt-dlp signe ses requêtes avec SAPISID).
LOGIN_COOKIES = {"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID", "LOGIN_INFO"}


def cookie_source(cookies=None, browser=None):
    """Cookies de l'extension s'ils contiennent une session connectée, sinon yt-dlp lit lui-même
    ceux du navigateur réglé (terminal, IA, ou extension qui n'a pas pu lire les cookies)."""
    cookies = [c for c in cookies or () if isinstance(c, dict)]
    if any(c.get("name") in LOGIN_COOKIES for c in cookies):
        return CookieSource(cookies=cookies)
    if cookies:
        log(f"{len(cookies)} cookies reçus de l'extension, sans session connectée : lecture dans le navigateur")
    return CookieSource(browser=browser or load_config()["browser"])


# ---------- lancement de yt-dlp ----------

PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
ERROR_KINDS = (
    ("blocked", ("not a bot", "http error 429", "too many requests", "rate-limit", "rate limit",
                 "try again later", "unusual traffic")),
    ("auth", ("confirm your age", "age-restricted", "age restricted", "inappropriate for some users",
              "members-only", "members only", "join this channel", "private video", "registered users",
              "requires authentication", "login required", "sign in to view")),
    ("unavailable", ("video unavailable", "has been removed", "copyright", "not available in your country",
                     "not made this video available", "this live event will begin", "premieres in",
                     "unsupported url", "incomplete youtube id", "is not a valid url")),
    ("broken", ("http error 403", "forbidden", "requested format is not available", "unable to extract",
                "nsig", "n challenge", "signature", "no video formats", "precondition check failed",
                "player response", "only images are available", "challenge solving failed",
                "javascript runtime", "sabr", "unable to download api page", "po token")),
    ("network", ("timed out", "connection reset", "temporary failure in name resolution", "nodename nor servname",
                 "network is unreachable", "unable to download webpage", "connection refused",
                 "remote end closed", "eof occurred", "ssl")),
)


def classify(lines):
    text = "\n".join(lines[-40:]).lower().replace("’", "'")
    for kind, patterns in ERROR_KINDS:
        if any(p in text for p in patterns):
            return kind
    return "other"


def error_line(lines, code):
    line = next((l for l in reversed(lines) if l.startswith("ERROR")), None)
    line = line or next((l for l in reversed(lines) if "error" in l.lower()), None)
    line = line or (lines[-1] if lines else f"yt-dlp a quitté avec le code {code}")
    return re.sub(r"^ERROR:\s*", "", line)[:300]


def friendly_error(kind, raw, used_cookies):
    if kind == "blocked":
        hint = "" if used_cookies else " Connecte-toi à YouTube dans ton navigateur : ta session servira de secours."
        return ("YouTube bloque temporairement les téléchargements depuis ton adresse IP (anti-robot ou trop de "
                "requêtes). Réessaie dans 15 à 60 minutes, les prochains seront espacés." + hint + f" (yt-dlp : {raw})")
    if kind == "auth":
        hint = ("Ta session YouTube n'y a pas accès." if used_cookies else
                "Connecte-toi à YouTube dans ton navigateur et laisse la session YouTube sur « en secours » ou « toujours ».")
        return f"Cette vidéo demande d'être connecté (âge, membres ou vidéo privée). {hint} (yt-dlp : {raw})"
    if kind == "broken":
        return ("YouTube a changé quelque chose et yt-dlp n'arrive plus à lire la vidéo, même après mise à jour. "
                f"Réessaie plus tard (yt-dlp : {raw})")
    return raw


def base_args(exe, careful):
    args = ytdlp_cmd(exe) + ["--ignore-config", "--no-colors", "--newline", "--no-playlist",
            "--retries", "10", "--fragment-retries", "10", "--extractor-retries", "3",
            "--retry-sleep", "exp=1:30", "--retry-sleep", "fragment:exp=1:30", "--retry-sleep", "extractor:exp=2:20",
            "--socket-timeout", "30", "--remote-components", "ejs:github"]
    name, path = js_runtime()
    if name:
        args += ["--js-runtimes", f"{name}:{path}"]
    if careful:
        args += ["--sleep-requests", "1", "--sleep-subtitles", "3"]
    return args


def run_ytdlp(cmd, job, on_line=None, capture=False):
    """Lance yt-dlp. Renvoie (code, stdout si capture, lignes de sortie)."""
    log("yt-dlp " + " ".join(a if " " not in a else repr(a) for a in cmd[1:]))
    env = dict(os.environ, PYTHONUNBUFFERED="1")  # sinon la progression n'arrive qu'à la fin
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE if capture else subprocess.STDOUT,
                            text=True, bufsize=1, env=env)
    job.attach(proc)
    lines = []
    if capture:
        out, err = proc.communicate()
        lines = [l for l in err.splitlines() if l.strip()]
    else:
        out = None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            lines.append(line)
            if not PROGRESS_RE.search(line) or "100%" in line:
                log("  " + line)
            if on_line:
                on_line(line)
    code = proc.wait()
    for line in lines[-15:] if capture else []:
        log("  " + line)
    log(f"code de sortie {code}")
    job.check()
    return code, out, lines


def run_with_strategy(build, report=_no_report, job=None, cookies=None, cookies_mode=None, capture=False,
                      make_parser=None):
    """Lance yt-dlp avec la stratégie anti-blocage. build(options) renvoie les arguments propres
    à l'appel ; make_parser() fournit, pour chaque essai, une fonction appelée sur chaque ligne."""
    job = job or Job()
    cookies = cookies or CookieSource()
    mode = cookies_mode or load_config()["cookies"]
    can_cookies = mode != "never" and cookies.available()
    exe, managed = find_ytdlp()
    if not exe:
        raise YtdlError("yt-dlp introuvable : lance host/installer.command (ou brew install yt-dlp)", "missing")
    if not shutil.which("ffmpeg"):
        raise YtdlError("ffmpeg introuvable (brew install ffmpeg)", "missing")
    update_ytdlp(report, job)

    # Après un blocage récent (mode prudent), on commence directement avec la session YouTube :
    # inutile de se refaire bloquer en anonyme.
    careful_now = read_state().get("careful_until", 0) > time.time()
    opts = {"cookies": can_cookies and (mode == "always" or careful_now), "clients": None}
    tried_update = tried_pause = quick = False
    network_retries = 0
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            careful = wait_turn(report, job, quick)
            quick = False
            if attempt > 1:
                report(phase="preparing", note=f"Essai {attempt}…")
            cmd = base_args(exe, careful) + (cookies.args() if opts["cookies"] else [])
            if opts["clients"]:
                cmd += ["--extractor-args", "youtube:player_client=" + opts["clients"]]
            cmd += build(opts)
            code, out, lines = run_ytdlp(cmd, job, make_parser() if make_parser else None, capture)
            if code == 0:
                return out, lines
            kind = classify(lines)
            raw = error_line(lines, code)
            log(f"échec ({kind}), essai {attempt}, options {opts}")
            if kind in ("blocked", "auth"):
                if kind == "blocked":
                    mark_careful()
                if not opts["cookies"] and can_cookies:
                    opts["cookies"] = quick = True
                    continue
                if kind == "blocked" and not tried_pause:
                    tried_pause = True
                    pause(BLOCK_PAUSE, report, job)
                    continue
                if not opts["clients"]:
                    opts["clients"] = FALLBACK_CLIENTS
                    continue
            elif kind == "broken":
                if not tried_update and update_ytdlp(report, job, force=True):
                    tried_update = True
                    continue
                if not opts["clients"]:
                    opts["clients"] = FALLBACK_CLIENTS
                    continue
                if not opts["cookies"] and can_cookies:
                    opts["cookies"] = True
                    continue
            elif kind == "network" and network_retries < 2:
                network_retries += 1
                pause(5 * network_retries, report, job)
                continue
            raise YtdlError(friendly_error(kind, raw, opts["cookies"]), kind)
        raise YtdlError(friendly_error(kind, raw, opts["cookies"]), kind)
    finally:
        cookies.cleanup()


# ---------- téléchargement ----------

def format_args(fmt, max_height):
    """Format (mp4 / mp3) et hauteur maximale ('best' = sans limite). Avec une limite, yt-dlp prend
    la meilleure qualité disponible en dessous : si la vidéo n'existe qu'en 240p et que la limite
    est 480p, il prend 240p."""
    if fmt == "mp3":
        return ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    if max_height in MAX_HEIGHTS:
        selector = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
    else:
        selector = "bv*+ba/b"
    return ["-f", selector, "--merge-output-format", "mp4"]


SECTION_RE = re.compile(r"^\d+(:\d{1,2}){0,2}(\.\d+)?-(\d+(:\d{1,2}){0,2}(\.\d+)?|inf)$")


def download(url, fmt=None, max_height=None, directory=None, section=None, cookies=None, cookies_mode=None,
             report=_no_report, job=None):
    """Télécharge une vidéo ; renvoie {title, filepath, filename, id, duration, size}."""
    url = check_url(url)
    config = load_config()
    fmt = "mp3" if str(fmt or config["format"]) == "mp3" else "mp4"
    max_height = str(max_height or config["max_height"])
    out_dir = resolve_dir(directory)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise YtdlError(f"Dossier de destination inaccessible : {exc}", "invalid")
    template = "%(title)s [%(id)s].%(ext)s"
    extra = []
    if section:
        section = str(section).replace(" ", "")
        if not SECTION_RE.match(section):
            raise YtdlError("Extrait invalide : utilise DÉBUT-FIN, par exemple 1:30-2:45", "invalid")
        extra = ["--download-sections", "*" + section]
        template = f"%(title)s [%(id)s] ({section.replace(':', '.')}).%(ext)s"
    result = {}

    def build(_opts):
        return ["--no-simulate", "--progress", "-P", str(out_dir), "-o", template,
                "--print", "before_dl:TITLE:%(title)s",
                "--print", "before_dl:META:%(id)s|%(duration)s",
                "--print", "after_move:FILEPATH:%(filepath)s"] + format_args(fmt, max_height) + extra + ["--", url]

    def make_parser():
        # yt-dlp télécharge la vidéo puis l'audio : chaque flux repart de 0 %. On affiche une
        # progression globale : vidéo 0 -> 92 %, audio 92 -> 100 %.
        st = {"raw": 0.0, "stream": 0, "progress": 0.0, "sent": -1.0}

        def on_line(line):
            if line.startswith("TITLE:"):
                result["title"] = line[6:].strip()
                report(phase="downloading", progress=st["progress"], title=result["title"])
            elif line.startswith("META:"):
                vid, _, duration = line[5:].strip().partition("|")
                result["id"] = vid
                with contextlib.suppress(ValueError):
                    result["duration"] = round(float(duration))
            elif line.startswith("FILEPATH:"):
                result["filepath"] = line[9:].strip()
            elif PROGRESS_RE.search(line):
                raw = float(PROGRESS_RE.search(line).group(1))
                if raw < st["raw"] - 5:
                    st["stream"] += 1
                st["raw"] = raw
                pct = raw * 0.92 if st["stream"] == 0 else 92 + raw * 0.08
                st["progress"] = pct = round(min(100.0, max(st["progress"], pct)), 1)
                if abs(pct - st["sent"]) >= 1 or pct >= 100:
                    st["sent"] = pct
                    report(phase="downloading", progress=pct)
            elif "[Merger]" in line or "[ExtractAudio]" in line or "[ffmpeg]" in line:
                report(phase="processing", progress=100)
        return on_line

    report(phase="preparing", note="Préparation…")
    run_with_strategy(build, report, job, cookies, cookies_mode, make_parser=make_parser)
    path = result.get("filepath")
    if not path or not os.path.exists(path):
        raise YtdlError("yt-dlp a terminé sans indiquer de fichier", "other")
    result.update(filepath=path, filename=os.path.basename(path), format=fmt,
                  size=os.path.getsize(path), title=result.get("title") or os.path.basename(path))
    log(f"terminé : {path}")
    return result


# ---------- informations, recherche, transcription ----------

def fetch_info(url, flat=True, cookies=None, cookies_mode=None, report=_no_report, job=None):
    def build(_opts):
        return ["-J", "--skip-download"] + (["--flat-playlist"] if flat else []) + ["--", url]

    out, _ = run_with_strategy(build, report, job, cookies, cookies_mode, capture=True)
    try:
        return json.loads(out)
    except ValueError:
        raise YtdlError("Réponse de yt-dlp illisible", "other")


def hms(seconds):
    if seconds is None:
        return None
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def info(url, cookies=None, cookies_mode=None, report=_no_report, job=None):
    """Métadonnées utiles d'une vidéo (sans téléchargement)."""
    data = fetch_info(check_url(url), flat=False, cookies=cookies, cookies_mode=cookies_mode, report=report, job=job)
    date = data.get("upload_date") or ""
    heights = sorted({f["height"] for f in data.get("formats") or [] if f.get("height") and f.get("vcodec") != "none"})
    description = data.get("description") or ""
    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "url": data.get("webpage_url"),
        "channel": data.get("channel") or data.get("uploader"),
        "channel_url": data.get("channel_url"),
        "duration": data.get("duration"),
        "duration_string": hms(data.get("duration")),
        "upload_date": f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else None,
        "view_count": data.get("view_count"),
        "like_count": data.get("like_count"),
        "language": data.get("language"),
        "is_live": data.get("is_live"),
        "max_height": heights[-1] if heights else None,
        "chapters": [{"start": hms(c.get("start_time")), "title": c.get("title")} for c in data.get("chapters") or []],
        "subtitles": sorted(k for k in (data.get("subtitles") or {}) if k != "live_chat"),
        "automatic_captions": bool(data.get("automatic_captions")),
        "tags": (data.get("tags") or [])[:15],
        "thumbnail": data.get("thumbnail"),
        "description": description[:4000] + ("…" if len(description) > 4000 else ""),
    }


def search(query, limit=5, cookies=None, cookies_mode=None, report=_no_report, job=None):
    query = str(query or "").strip()
    if not query:
        raise YtdlError("Recherche vide", "invalid")
    limit = max(1, min(20, int(limit or 5)))
    data = fetch_info(f"ytsearch{limit}:{query}", cookies=cookies, cookies_mode=cookies_mode, report=report, job=job)
    results = []
    for e in data.get("entries") or []:
        results.append({
            "title": e.get("title"),
            "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
            "channel": e.get("channel") or e.get("uploader"),
            "duration_string": hms(e.get("duration")),
            "view_count": e.get("view_count"),
        })
    return results


def pick_subtitle(data, lang=None):
    """(clé de la piste, automatique ?) : sous-titres manuels d'abord, dans la langue demandée,
    sinon dans la langue de la vidéo ; les sous-titres automatiques en secours."""
    manual = [k for k in (data.get("subtitles") or {}) if k != "live_chat"]
    auto = list(data.get("automatic_captions") or {})
    orig_auto = next((k for k in auto if k.endswith("-orig")), None)
    original = (data.get("language") or (orig_auto or "")[:-5] or "").lower()

    def find(tracks, code):
        if not code:
            return None
        code = code.lower()
        base = code.split("-")[0]
        return (next((k for k in tracks if k.lower() == code), None)
                or next((k for k in tracks if k.lower().split("-")[0] == base and not k.endswith("-orig")), None))

    candidates = []
    if lang:
        same_as_original = original.split("-")[0] == lang.lower().split("-")[0]
        candidates += [(find(manual, lang), False), (orig_auto if same_as_original else None, True),
                       (find(auto, lang), True)]
    candidates += [(find(manual, original), False), (orig_auto, True), (find(auto, original), True),
                   (manual[0] if manual else None, False), (find(auto, "en"), True)]
    return next(((k, a) for k, a in candidates if k), (None, None))


def parse_json3(text):
    segments = []
    for event in json.loads(text).get("events") or []:
        line = "".join(s.get("utf8", "") for s in event.get("segs") or []).replace("\n", " ").strip()
        if line:
            segments.append((event.get("tStartMs", 0) / 1000, re.sub(r"\s+", " ", line)))
    return segments


def parse_vtt(text):
    segments, last = [], None
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip().splitlines()
        timing = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if timing is None:
            continue
        parts = [float(p) for p in re.findall(r"[\d.]+", lines[timing].split("-->")[0])]
        start = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
        body = html.unescape(" ".join(re.sub(r"<[^>]+>", "", l).strip() for l in lines[timing + 1:])).strip()
        if body and body != last:
            segments.append((start, body))
            last = body
    return segments


def format_transcript(segments, timestamps=False):
    if timestamps:
        return "\n".join(f"[{hms(t)}] {text}" for t, text in segments)
    paragraphs, current, start = [], [], None
    for t, text in segments:
        start = t if start is None else start
        current.append(text)
        if (t - start >= 45 and text.endswith((".", "?", "!", "…"))) or t - start >= 90:
            paragraphs.append(" ".join(current))
            current, start = [], None
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def transcript(url, lang=None, timestamps=False, cookies=None, cookies_mode=None, report=_no_report, job=None):
    """Transcription d'une vidéo (sous-titres), sans télécharger la vidéo."""
    url = check_url(url)
    job = job or Job()
    data = fetch_info(url, flat=False, cookies=cookies, cookies_mode=cookies_mode, report=report, job=job)
    key, automatic = pick_subtitle(data, lang)
    if not key:
        raise YtdlError("Cette vidéo n'a ni sous-titres ni sous-titres automatiques.", "unavailable")
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    work = tempfile.mkdtemp(prefix="subs-", dir=TMP_DIR)
    try:
        def build(_opts):
            return ["--skip-download", "--write-auto-subs" if automatic else "--write-subs",
                    "--sub-langs", re.escape(key), "--sub-format", "json3/vtt/best",
                    "-P", work, "-o", "sub.%(ext)s", "--", url]

        run_with_strategy(build, report, job, cookies, cookies_mode)
        files = sorted(Path(work).glob("sub.*"))
        if not files:
            raise YtdlError("yt-dlp n'a pas récupéré les sous-titres", "other")
        raw = files[0].read_text(encoding="utf-8", errors="replace")
        segments = parse_json3(raw) if files[0].suffix == ".json3" else parse_vtt(raw)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "channel": data.get("channel") or data.get("uploader"),
        "duration_string": hms(data.get("duration")),
        "language": key[:-5] if key.endswith("-orig") else key,
        "automatic": automatic,
        "text": format_transcript(segments, timestamps),
    }
