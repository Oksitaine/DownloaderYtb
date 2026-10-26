#!/usr/bin/env python3
"""Hôte natif (Chrome Native Messaging) pour l'extension "YouTube Download Button".

Chrome lance ce script à la demande et lui parle sur stdin/stdout (messages JSON précédés
de leur longueur sur 4 octets). Le script appelle yt-dlp, renvoie la progression, puis se
termine quand Chrome ferme la connexion. Aucun serveur, aucun port réseau.

Messages reçus :
  {"type": "ping"}                                  -> {"type": "pong", "ytdlp_version", "ffmpeg", "download_dir"}
  {"type": "download", "id", "url", "format", "maxHeight", "dir"} -> suite de {"type": "progress"} puis {"type": "done"} ou {"type": "error"}
  {"type": "reveal", "filepath"}                    -> {"type": "revealed"} (ouvre le Finder sur le fichier)
  {"type": "choose-folder", "current"}              -> {"type": "folder", "path"} (sélecteur de dossier macOS)
"""

import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

DOWNLOAD_DIR = Path(os.environ.get("YTB_DL_DIR", str(Path.home() / "Downloads" / "YouTube")))

# Chrome lance l'hôte avec un PATH minimal : on ajoute les emplacements de Homebrew.
for extra in ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")):
    if extra not in os.environ.get("PATH", ""):
        os.environ["PATH"] = extra + os.pathsep + os.environ.get("PATH", "")

ALLOWED_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}

MAX_HEIGHTS = {"2160", "1440", "1080", "720", "480", "360", "240", "144"}


def format_args(fmt, max_height):
    """Arguments yt-dlp selon le format (mp4 / mp3) et la hauteur maximale ('best' = sans limite).
    Avec une limite, yt-dlp prend la meilleure qualité disponible en dessous : si la vidéo
    n'existe qu'en 240p et que la limite est 480p, il prend 240p."""
    if fmt == "mp3":
        return ["-x", "--audio-format", "mp3", "--audio-quality", "0"]
    if max_height in MAX_HEIGHTS:
        selector = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
    else:
        selector = "bv*+ba/b"
    return ["-f", selector, "--merge-output-format", "mp4"]


PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")


# ---------- protocole Native Messaging ----------

def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) < 4:
        return None
    (length,) = struct.unpack("=I", raw_length)
    data = sys.stdin.buffer.read(length)
    if len(data) < length:
        return None
    return json.loads(data.decode("utf-8"))


def send_message(message):
    encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


# ---------- outils ----------

def find_ytdlp():
    return shutil.which("yt-dlp")


def ytdlp_version():
    exe = find_ytdlp()
    if not exe:
        return None
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        return None


def valid_youtube_url(url):
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and parsed.hostname in ALLOWED_HOSTS


# ---------- commandes ----------

def handle_ping():
    send_message({
        "type": "pong",
        "ytdlp_version": ytdlp_version(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "download_dir": str(DOWNLOAD_DIR),
    })


def handle_reveal(message):
    path = str(message.get("filepath", ""))
    if path and os.path.exists(path):
        subprocess.Popen(["open", "-R", path])
        send_message({"type": "revealed"})
    else:
        send_message({"type": "error", "error": "Fichier introuvable"})


def resolve_download_dir(value):
    """Dossier choisi dans le popup (chaîne vide = dossier par défaut)."""
    text = str(value or "").strip()
    if not text:
        return DOWNLOAD_DIR
    return Path(os.path.expanduser(text))


def handle_choose_folder(message):
    current = resolve_download_dir(message.get("current"))
    if not current.is_dir():
        current = Path.home() / "Downloads"
    script = (
        'POSIX path of (choose folder with prompt "Dossier où enregistrer les vidéos YouTube" '
        f'default location POSIX file "{str(current).replace(chr(34), "")}")'
    )
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
    except Exception as exc:
        return send_message({"type": "error", "error": f"Sélecteur indisponible : {exc}"})
    path = result.stdout.strip().rstrip("/")
    if result.returncode != 0 or not path:
        return send_message({"type": "folder", "path": None, "cancelled": True})
    send_message({"type": "folder", "path": path})


def handle_download(message):
    job_id = str(message.get("id", ""))
    url = str(message.get("url", "")).strip()
    fmt = "mp3" if str(message.get("format", "mp4")) == "mp3" else "mp4"
    max_height = str(message.get("maxHeight", "best"))
    download_dir = resolve_download_dir(message.get("dir"))

    if not valid_youtube_url(url):
        return send_message({"type": "error", "id": job_id, "error": "URL YouTube invalide"})
    exe = find_ytdlp()
    if not exe:
        return send_message({"type": "error", "id": job_id, "error": "yt-dlp introuvable (brew install yt-dlp)"})
    if not shutil.which("ffmpeg"):
        return send_message({"type": "error", "id": job_id, "error": "ffmpeg introuvable (brew install ffmpeg)"})

    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return send_message({"type": "error", "id": job_id, "error": f"Dossier de destination inaccessible : {exc}"})
    cmd = [
        exe,
        "--no-playlist",
        "--newline",
        "--progress",  # --print active le mode silencieux : --progress force l'affichage de la progression
        "--no-colors",
        "--no-warnings",
        "-P", str(download_dir),
        "-o", "%(title)s [%(id)s].%(ext)s",
        "--print", "before_dl:TITLE:%(title)s",
        "--print", "after_move:FILEPATH:%(filepath)s",
        "--no-simulate",
    ] + format_args(fmt, max_height) + [url]

    # yt-dlp est un programme Python : sans PYTHONUNBUFFERED, sa sortie redirigée vers un tube
    # est mise en tampon et la progression n'arriverait qu'à la fin.
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    except Exception as exc:
        return send_message({"type": "error", "id": job_id, "error": f"Impossible de lancer yt-dlp : {exc}"})

    title = None
    filepath = None
    progress = 0.0
    last_sent = -1.0
    last_raw = 0.0
    stream_index = 0
    last_lines = []
    send_message({"type": "progress", "id": job_id, "phase": "downloading", "progress": 0})

    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        last_lines.append(line)
        last_lines = last_lines[-15:]

        if line.startswith("TITLE:"):
            title = line[len("TITLE:"):].strip()
            send_message({"type": "progress", "id": job_id, "phase": "downloading", "progress": progress, "title": title})
            continue
        if line.startswith("FILEPATH:"):
            filepath = line[len("FILEPATH:"):].strip()
            continue

        m = PROGRESS_RE.search(line)
        if m:
            raw = float(m.group(1))
            # yt-dlp télécharge la vidéo puis l'audio : chaque flux repart de 0 %.
            # On affiche une progression globale : vidéo 0 -> 92 %, audio 92 -> 100 %.
            if raw < last_raw - 5:
                stream_index += 1
            last_raw = raw
            pct = raw * 0.92 if stream_index == 0 else 92 + raw * 0.08
            pct = round(min(100.0, max(progress, pct)), 1)
            progress = pct
            if abs(pct - last_sent) >= 1 or pct >= 100:
                last_sent = pct
                send_message({"type": "progress", "id": job_id, "phase": "downloading", "progress": pct})
            continue

        if "[Merger]" in line or "[ExtractAudio]" in line or "[ffmpeg]" in line:
            send_message({"type": "progress", "id": job_id, "phase": "processing", "progress": 100})

    code = proc.wait()
    if code == 0 and filepath:
        send_message({
            "type": "done",
            "id": job_id,
            "title": title,
            "filename": os.path.basename(filepath),
            "filepath": filepath,
        })
    else:
        error_line = next(
            (l for l in reversed(last_lines) if "ERROR" in l or "error" in l.lower()),
            last_lines[-1] if last_lines else f"yt-dlp a quitté avec le code {code}",
        )
        error_line = re.sub(r"^ERROR:\s*", "", error_line)
        send_message({"type": "error", "id": job_id, "error": error_line[:400]})


def main():
    while True:
        try:
            message = read_message()
        except Exception:
            break
        if message is None:
            break
        kind = message.get("type")
        if kind == "ping":
            handle_ping()
        elif kind == "download":
            handle_download(message)
        elif kind == "reveal":
            handle_reveal(message)
        elif kind == "choose-folder":
            handle_choose_folder(message)
        else:
            send_message({"type": "error", "error": f"Message inconnu : {kind}"})


if __name__ == "__main__":
    main()
