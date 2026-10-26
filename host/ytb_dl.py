#!/usr/bin/env python3
"""ytb-dl : YouTube en ligne de commande et pour les IA (Claude Code...), avec la même stratégie
anti-blocage que l'extension (ytb_core) et les mêmes réglages (dossier, format, qualité).

    ytb-dl URL [URL...] [--mp3] [--max-height 720] [--dir DOSSIER] [--section 1:30-2:45] [--json]
    ytb-dl transcript URL [--lang fr] [--timestamps] [-o FICHIER] [--json]
    ytb-dl info URL [--json]
    ytb-dl search "requête" [--limit 5] [--json]
    ytb-dl update | doctor | config [CLÉ [VALEUR]]
    ytb-dl mcp      serveur MCP sur stdin/stdout (outils download, transcript, info, search)

Option commune : --cookies auto|always|never (session YouTube du navigateur : en secours, toujours,
jamais ; navigateur réglé par « ytb-dl config browser chrome »).
"""

import argparse
import json
import sys
import threading
import time

import ytb_core as core

COMMANDS = ("download", "transcript", "info", "search", "update", "doctor", "config", "mcp")
MCP_TEXT_LIMIT = 60000  # au-delà, la transcription complète est enregistrée dans un fichier


# ---------- terminal ----------

def stderr_reporter():
    tty = sys.stderr.isatty()
    last = {"line": None}

    def report(phase, progress=None, note=None, title=None):
        if title:
            line = f"▶ {title}"
        elif note:
            line = f"… {note}"
        elif progress is not None:
            if not tty and int(progress) % 10 and progress < 100:
                return
            line = "  finalisation…" if phase == "processing" else f"  {progress:5.1f} %"
        else:
            return
        if line == last["line"]:
            return
        if tty and last["line"] and last["line"].startswith("  ") and line.startswith("  "):
            sys.stderr.write("\r" + line.ljust(20))
        else:
            sys.stderr.write(("\n" if tty and last["line"] and last["line"].startswith("  ") else "") + line
                             + ("" if tty and line.startswith("  ") else "\n"))
        sys.stderr.flush()
        last["line"] = line
    return report


def emit(args, payload, text):
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif text is not None:
        print(text)


def cmd_download(args):
    results, failed = [], False
    for url in args.urls:
        try:
            result = core.download(url, "mp3" if args.mp3 else args.format, args.max_height, args.dir, args.section,
                                   cookies=core.cookie_source(), cookies_mode=args.cookies, report=stderr_reporter())
            if sys.stderr.isatty():
                sys.stderr.write("\n")
            results.append(result)
            if not args.json:
                print(result["filepath"])
        except core.YtdlError as exc:
            failed = True
            results.append({"url": url, "error": str(exc), "kind": exc.kind})
            sys.stderr.write(f"\nErreur ({url}) : {exc}\n")
    if args.json:
        print(json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def cmd_transcript(args):
    result = core.transcript(args.url, args.lang, args.timestamps, cookies=core.cookie_source(),
                             cookies_mode=args.cookies, report=stderr_reporter())
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result["text"] + "\n")
        result = dict(result, text=None, file=args.output)
        sys.stderr.write(f"Transcription enregistrée : {args.output}\n")
    emit(args, result, result["text"])
    return 0


def cmd_info(args):
    result = core.info(args.url, cookies=core.cookie_source(), cookies_mode=args.cookies, report=stderr_reporter())
    lines = [f"{result['title']}", f"{result['channel']} · {result['duration_string']} · {result['upload_date']}"
             f" · {result['view_count']} vues · max {result['max_height']}p", result["url"]]
    if result["chapters"]:
        lines += ["", "Chapitres :"] + [f"  {c['start']}  {c['title']}" for c in result["chapters"]]
    lines += ["", "Sous-titres : " + (", ".join(result["subtitles"]) or "aucun")
              + (" (+ automatiques)" if result["automatic_captions"] else ""), "", result["description"]]
    emit(args, result, "\n".join(lines))
    return 0


def cmd_search(args):
    results = core.search(args.query, args.limit, cookies=core.cookie_source(), cookies_mode=args.cookies,
                          report=stderr_reporter())
    emit(args, results, "\n".join(f"{r['title']}  ({r['channel']}, {r['duration_string']})\n  {r['url']}"
                                  for r in results))
    return 0


def cmd_update(args):
    exe, managed = core.find_ytdlp()
    if not managed:
        sys.stderr.write("yt-dlp n'est pas géré par ytb-dl : lance host/installer.command (ou brew upgrade yt-dlp).\n")
        return 1
    core.update_ytdlp(stderr_reporter(), always=True)
    print(core.ytdlp_version(exe))
    return 0


def cmd_doctor(args):
    h = core.health()
    config = core.load_config()
    if args.json:
        print(json.dumps({"health": h, "config": config}, ensure_ascii=False, indent=2))
        return 0
    age = h["ytdlp_age_days"]
    rows = [
        ("yt-dlp", f"{h['ytdlp_version']} ({'géré, mis à jour automatiquement' if h['ytdlp_managed'] else 'non géré'})"
                   f" — {h['ytdlp_path']}" if h["ytdlp_version"] else "INTROUVABLE : lance host/installer.command"),
        ("ancienneté", f"{age} jours" if age is not None else "?"),
        ("ffmpeg", "ok" if h["ffmpeg"] else "INTROUVABLE : brew install ffmpeg"),
        ("JavaScript", h["js_runtime"] or "AUCUN : brew install deno (YouTube en a besoin)"),
        ("mode prudent", time.strftime("jusqu'à %H:%M", time.localtime(h["careful_until"])) if h["careful_until"] else "non"),
        ("dossier", h["download_dir"]),
        ("réglages", ", ".join(f"{k}={v or '(défaut)'}" for k, v in config.items())),
        ("journal", h["log_file"]),
    ]
    for key, value in rows:
        print(f"{key:>13} : {value}")
    if not h["ytdlp_managed"] and age is not None and age > 30:
        print("\nyt-dlp date de plus d'un mois : YouTube le bloque souvent. Relance host/installer.command.")
    return 0


def cmd_config(args):
    if args.key is None:
        emit(args, core.load_config(), json.dumps(core.load_config(), ensure_ascii=False, indent=2))
        return 0
    if args.value is None:
        print(core.load_config().get(args.key, ""))
        return 0
    emit(args, core.save_config({args.key: args.value}), "ok")
    return 0


# ---------- serveur MCP (JSON-RPC sur stdin/stdout, une ligne par message) ----------

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
URL_PROP = {"type": "string", "description": "URL d'une vidéo YouTube (youtube.com/watch?v=..., youtu.be/..., /shorts/...)"}
MCP_TOOLS = [
    {
        "name": "transcript",
        "description": ("Transcription d'une vidéo YouTube (sous-titres manuels, sinon automatiques), sans télécharger "
                        "la vidéo. À privilégier pour résumer, citer, analyser ou répondre à une question sur une vidéo."),
        "inputSchema": {"type": "object", "required": ["url"], "properties": {
            "url": URL_PROP,
            "lang": {"type": "string", "description": "Langue voulue (fr, en...). Par défaut : langue de la vidéo."},
            "timestamps": {"type": "boolean", "description": "Préfixer chaque ligne par son horodatage [m:ss]."},
        }},
    },
    {
        "name": "download",
        "description": ("Télécharge une vidéo YouTube (MP4) ou seulement son audio (MP3) sur cet ordinateur avec yt-dlp, "
                        "en contournant les blocages de YouTube (yt-dlp tenu à jour, session YouTube du navigateur en "
                        "secours, pauses). Renvoie le chemin du fichier. Peut prendre plusieurs minutes."),
        "inputSchema": {"type": "object", "required": ["url"], "properties": {
            "url": URL_PROP,
            "format": {"type": "string", "enum": ["mp4", "mp3"], "description": "mp4 (vidéo) ou mp3 (audio seul)."},
            "max_height": {"type": "string", "enum": ["best", "2160", "1440", "1080", "720", "480", "360", "240", "144"],
                           "description": "Qualité maximale ; la meilleure en dessous est prise si besoin."},
            "directory": {"type": "string", "description": "Dossier de destination (par défaut celui de l'extension)."},
            "section": {"type": "string", "description": "Extrait seulement, DÉBUT-FIN, par exemple 1:30-2:45."},
        }},
    },
    {
        "name": "info",
        "description": ("Métadonnées d'une vidéo YouTube : titre, chaîne, durée, date, vues, description, chapitres, "
                        "langues de sous-titres, qualité maximale."),
        "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": URL_PROP}},
    },
    {
        "name": "search",
        "description": "Recherche des vidéos sur YouTube ; renvoie titres, chaînes, durées et URLs.",
        "inputSchema": {"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": "Termes de recherche."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Nombre de résultats (5 par défaut)."},
        }},
    },
]


def mcp_call(name, arguments, report, job):
    a = arguments or {}
    cookies = core.cookie_source()
    if name == "download":
        result = core.download(a.get("url"), a.get("format"), a.get("max_height"), a.get("directory"), a.get("section"),
                               cookies=cookies, report=report, job=job)
        return json.dumps(result, ensure_ascii=False, indent=2)
    if name == "transcript":
        result = core.transcript(a.get("url"), a.get("lang"), bool(a.get("timestamps")), cookies=cookies,
                                 report=report, job=job)
        text = result.pop("text")
        header = json.dumps(result, ensure_ascii=False)
        if len(text) > MCP_TEXT_LIMIT:
            folder = core.APP_DIR / "transcripts"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{result['id']}.{result['language']}.txt"
            path.write_text(text + "\n", encoding="utf-8")
            text = (text[:MCP_TEXT_LIMIT] + f"\n\n[… transcription tronquée : texte complet ({len(text)} caractères) "
                    f"dans {path}]")
        return header + "\n\n" + text
    if name == "info":
        return json.dumps(core.info(a.get("url"), cookies=cookies, report=report, job=job), ensure_ascii=False, indent=2)
    if name == "search":
        return json.dumps(core.search(a.get("query"), a.get("limit") or 5, cookies=cookies, report=report, job=job),
                          ensure_ascii=False, indent=2)
    raise core.YtdlError(f"Outil inconnu : {name}", "invalid")


def serve_mcp():
    out_lock = threading.Lock()
    running = {}  # id de requête -> Job, pour les annulations

    def send(message):
        with out_lock:
            sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def reply(request_id, result=None, error=None):
        message = {"jsonrpc": "2.0", "id": request_id}
        message.update({"error": error} if error else {"result": result})
        send(message)

    def call_tool(request_id, params):
        job = core.Job()
        running[request_id] = job
        token = (params.get("_meta") or {}).get("progressToken")
        sent = {"progress": 0.0, "at": 0.0}

        def report(phase, progress=None, note=None, title=None):
            if token is None or time.time() - sent["at"] < 1:
                return
            # La progression doit croître à chaque notification.
            value = max(sent["progress"] + 0.01, float(progress or 0))
            sent.update(progress=value, at=time.time())
            message = note or title or ("Finalisation…" if phase == "processing" else f"{value:.0f} %")
            send({"jsonrpc": "2.0", "method": "notifications/progress",
                  "params": {"progressToken": token, "progress": value, "total": 100, "message": message}})

        try:
            text = mcp_call(params.get("name"), params.get("arguments"), report, job)
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except core.Cancelled:
            return  # le client a annulé : pas de réponse
        except core.YtdlError as exc:
            result = {"content": [{"type": "text", "text": f"Erreur ({exc.kind}) : {exc}"}], "isError": True}
        except Exception as exc:
            core.log(f"MCP : erreur inattendue : {exc!r}")
            result = {"content": [{"type": "text", "text": f"Erreur inattendue : {exc}"}], "isError": True}
        finally:
            running.pop(request_id, None)
        reply(request_id, result)

    instructions = (
        "Outils YouTube locaux (yt-dlp sur cet ordinateur, avec contournement des blocages). Pour comprendre ou résumer "
        "une vidéo, utiliser transcript (rapide, sans téléchargement) plutôt que download. download enregistre dans "
        f"{core.resolve_dir()} par défaut et renvoie le chemin du fichier."
    )
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply(None, error={"code": -32700, "message": "JSON invalide"})
            continue
        if not isinstance(message, dict):
            reply(None, error={"code": -32600, "message": "Requête invalide"})
            continue
        method, request_id, params = message.get("method"), message.get("id"), message.get("params") or {}
        if method == "initialize":
            asked = params.get("protocolVersion")
            reply(request_id, {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[1],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "youtube", "title": "YouTube (ytb-dl)", "version": core.VERSION},
                "instructions": instructions,
            })
        elif method == "ping":
            reply(request_id, {})
        elif method == "tools/list":
            reply(request_id, {"tools": MCP_TOOLS})
        elif method == "tools/call":
            threading.Thread(target=call_tool, args=(request_id, params), daemon=True).start()
        elif method == "notifications/cancelled":
            job = running.get(params.get("requestId"))
            if job:
                job.cancel()
        elif request_id is not None and not str(method).startswith("notifications/"):
            reply(request_id, error={"code": -32601, "message": f"Méthode inconnue : {method}"})
    for job in list(running.values()):
        job.cancel()
    return 0


# ---------- entrée ----------

def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="sortie JSON (pratique pour les scripts et les IA)")
    common.add_argument("--cookies", choices=core.COOKIE_MODES, default=None,
                        help="session YouTube du navigateur : auto (en secours, défaut), always, never")
    p = argparse.ArgumentParser(prog="ytb-dl", description="YouTube en local avec yt-dlp, anti-blocage compris.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n")[1])
    sub = p.add_subparsers(dest="command")
    d = sub.add_parser("download", parents=[common], help="télécharger (commande par défaut)")
    d.add_argument("urls", nargs="+", metavar="URL")
    d.add_argument("--format", choices=("mp4", "mp3"), default=None)
    d.add_argument("--mp3", action="store_true", help="audio seulement")
    d.add_argument("--max-height", choices=sorted(core.MAX_HEIGHTS, key=int) + ["best"], default=None)
    d.add_argument("--dir", default=None, help="dossier de destination")
    d.add_argument("--section", default=None, help="extrait DÉBUT-FIN, par exemple 1:30-2:45")
    t = sub.add_parser("transcript", parents=[common], help="transcription (sous-titres) sans télécharger la vidéo")
    t.add_argument("url")
    t.add_argument("--lang", default=None, help="langue voulue (fr, en...)")
    t.add_argument("--timestamps", action="store_true", help="horodatage [m:ss] devant chaque ligne")
    t.add_argument("-o", "--output", default=None, help="enregistrer dans ce fichier")
    i = sub.add_parser("info", parents=[common], help="métadonnées d'une vidéo")
    i.add_argument("url")
    s = sub.add_parser("search", parents=[common], help="rechercher sur YouTube")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)
    sub.add_parser("update", parents=[common], help="mettre à jour yt-dlp maintenant")
    sub.add_parser("doctor", parents=[common], help="diagnostic")
    c = sub.add_parser("config", parents=[common], help="afficher ou modifier les réglages partagés avec l'extension")
    c.add_argument("key", nargs="?", choices=sorted(core.DEFAULT_CONFIG))
    c.add_argument("value", nargs="?")
    sub.add_parser("mcp", help="serveur MCP pour les IA (Claude Code...)")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help"):
        argv.insert(0, "download")
    args = parser().parse_args(argv)
    if not args.command:
        parser().print_help()
        return 1
    if args.command == "mcp":
        return serve_mcp()
    handler = globals()["cmd_" + args.command]
    try:
        return handler(args)
    except core.YtdlError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc), "kind": exc.kind}, ensure_ascii=False, indent=2))
        sys.stderr.write(f"Erreur : {exc}\n")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
