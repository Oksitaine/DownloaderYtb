#!/usr/bin/env python3
"""Hôte natif (Chrome Native Messaging) pour l'extension "YouTube Download Button".

Chrome lance ce script à la demande et lui parle sur stdin/stdout (messages JSON précédés
de leur longueur sur 4 octets). Le script appelle yt-dlp (via ytb_core), renvoie la progression,
puis se termine quand Chrome ferme la connexion. Aucun serveur, aucun port réseau.

Messages reçus :
  {"type": "ping"}                                  -> {"type": "pong", ...état de yt-dlp, ffmpeg, dossier}
  {"type": "download", "id", "url", "format", "maxHeight", "dir", "cookies", "cookiesMode"}
                                                    -> suite de {"type": "progress"} puis {"type": "done"} ou {"type": "error"}
  {"type": "set-config", "config"}                  -> {"type": "config", "config"} (réglages partagés avec ytb-dl)
  {"type": "update"}                                -> {"type": "updated", "ytdlp_version"} (met à jour yt-dlp)
  {"type": "reveal", "filepath"}                    -> {"type": "revealed"} (ouvre le Finder sur le fichier)
  {"type": "choose-folder", "current"}              -> {"type": "folder", "path"} (sélecteur de dossier macOS)
"""

import json
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

import ytb_core as core

_write_lock = threading.Lock()
_jobs = []


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
    try:
        with _write_lock:
            sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
            sys.stdout.buffer.write(encoded)
            sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        # Chrome a fermé la connexion : inutile de continuer à télécharger pour personne.
        for job in _jobs:
            job.cancel()


# ---------- commandes ----------

def handle_ping():
    send_message(dict(core.health(), type="pong"))


def handle_reveal(message):
    path = str(message.get("filepath", ""))
    if path and os.path.exists(path):
        subprocess.Popen(["open", "-R", path])
        send_message({"type": "revealed"})
    else:
        send_message({"type": "error", "error": "Fichier introuvable"})


def handle_choose_folder(message):
    current = core.resolve_dir(message.get("current"))
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


def handle_set_config(message):
    config = message.get("config") or {}
    try:
        send_message({"type": "config", "config": core.save_config(config)})
    except core.YtdlError as exc:
        send_message({"type": "error", "error": str(exc)})


def handle_update():
    exe, managed = core.find_ytdlp()
    if not managed:
        return send_message({"type": "error", "error": "yt-dlp n'est pas géré par l'extension : relance host/installer.command"})
    core.update_ytdlp(always=True)
    send_message({"type": "updated", "ytdlp_version": core.ytdlp_version(exe)})


def handle_download(message, job):
    job_id = str(message.get("id", ""))
    fmt = "mp3" if str(message.get("format", "mp4")) == "mp3" else "mp4"
    max_height = str(message.get("maxHeight", "best"))
    mode = message.get("cookiesMode") if message.get("cookiesMode") in core.COOKIE_MODES else None
    # Réglages partagés avec ytb-dl : le terminal et les IA téléchargent au même endroit.
    try:
        core.save_config({"download_dir": message.get("dir") or "", "format": fmt, "max_height": max_height,
                          "cookies": mode})
    except core.YtdlError:
        pass

    def report(phase, progress=None, note=None, title=None):
        event = {"type": "progress", "id": job_id, "phase": phase}
        if progress is not None:
            event["progress"] = progress
        if note:
            event["note"] = note
        if title:
            event["title"] = title
        send_message(event)

    try:
        result = core.download(message.get("url"), fmt, max_height, message.get("dir"),
                               cookies=core.cookie_source(message.get("cookies")), cookies_mode=mode,
                               report=report, job=job)
    except core.Cancelled:
        return
    except core.YtdlError as exc:
        return send_message({"type": "error", "id": job_id, "error": str(exc), "kind": exc.kind})
    except Exception as exc:  # ne jamais laisser l'extension sans réponse
        core.log(f"erreur inattendue : {exc!r}")
        return send_message({"type": "error", "id": job_id, "error": f"Erreur inattendue : {exc}"})
    send_message({"type": "done", "id": job_id, "title": result["title"], "filename": result["filename"],
                  "filepath": result["filepath"]})


def main():
    threads = []
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
            # Dans un fil à part : la boucle continue de lire stdin pour savoir si Chrome coupe.
            job = core.Job()
            _jobs.append(job)
            thread = threading.Thread(target=handle_download, args=(message, job), daemon=True)
            thread.start()
            threads.append(thread)
        elif kind == "set-config":
            handle_set_config(message)
        elif kind == "update":
            handle_update()
        elif kind == "reveal":
            handle_reveal(message)
        elif kind == "choose-folder":
            handle_choose_folder(message)
        else:
            send_message({"type": "error", "error": f"Message inconnu : {kind}"})
    # Chrome a fermé la connexion : on arrête les téléchargements en cours.
    for job in _jobs:
        job.cancel()
    for thread in threads:
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
