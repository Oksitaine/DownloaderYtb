#!/bin/bash
# Installation (une fois par ordinateur, à relancer après une mise à jour du dossier host) :
# enregistre l'hôte natif auprès de Chrome, installe le yt-dlp géré (mis à jour automatiquement),
# la commande ytb-dl et le serveur MCP « youtube » pour Claude Code.
# Double-clic dans le Finder, ou : bash host/installer.command
#
# Prérequis : Homebrew avec ffmpeg, deno et Python 3.10+  ->  brew install ffmpeg deno python
# (brew install yt-dlp reste utile en secours, il installe aussi deno et python)

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_NAME="com.oksitaine.ytb_dl"
EXTENSION_ID="lpccinmpoibiiophngcnjbhmooccncdg"
ORIG_PATH="$PATH"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

# Les scripts sont copiés hors des dossiers protégés par macOS (Bureau, Documents...) :
# Chrome n'a pas forcément le droit d'y lire un programme, ~/Library l'autorise toujours.
INSTALL_DIR="$HOME/Library/Application Support/YouTubeDownloadButton"
HOST_SCRIPT="$INSTALL_DIR/ytb_dl_host.py"
mkdir -p "$INSTALL_DIR/bin"
cp "$DIR/ytb_core.py" "$DIR/ytb_dl_host.py" "$DIR/ytb_dl.py" "$INSTALL_DIR/"
chmod +x "$HOST_SCRIPT" "$INSTALL_DIR/ytb_dl.py"
echo "Programmes copiés : $INSTALL_DIR"

MANIFEST=$(cat <<JSON
{
  "name": "$HOST_NAME",
  "description": "Hôte natif de l'extension YouTube Download Button (yt-dlp)",
  "path": "$HOST_SCRIPT",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$EXTENSION_ID/"]
}
JSON
)

# Chrome et ses variantes lisent chacun leur propre dossier.
for BROWSER_DIR in \
  "$HOME/Library/Application Support/Google/Chrome" \
  "$HOME/Library/Application Support/Google/Chrome Beta" \
  "$HOME/Library/Application Support/Google/Chrome Canary" \
  "$HOME/Library/Application Support/Google/Chrome for Testing" \
  "$HOME/Library/Application Support/Chromium" \
  "$HOME/Library/Application Support/BraveSoftware/Brave-Browser" \
  "$HOME/Library/Application Support/Microsoft Edge" \
  "$HOME/Library/Application Support/Arc/User Data"
do
  if [ -d "$BROWSER_DIR" ] || [[ "$BROWSER_DIR" == *"/Google/Chrome" ]]; then
    mkdir -p "$BROWSER_DIR/NativeMessagingHosts"
    printf '%s\n' "$MANIFEST" > "$BROWSER_DIR/NativeMessagingHosts/$HOST_NAME.json"
    echo "Enregistré : $BROWSER_DIR/NativeMessagingHosts/$HOST_NAME.json"
  fi
done

# yt-dlp géré : version officielle en Python pur (zipapp) du canal nightly (les correctifs YouTube
# y arrivent en premier), vérifiée avec sa somme SHA-256 et lancée par un Python 3.10+. Elle se met
# ensuite à jour toute seule. Pas l'exécutable yt-dlp_macos : il décompresse son Python à chaque
# lancement, et macOS refuse de charger ces fichiers quand c'est Chrome qui lance le programme
# (Chrome met en quarantaine tout ce que ses programmes enfants écrivent).
echo
rm -f "$INSTALL_DIR/bin/yt-dlp"  # ancien exécutable yt-dlp_macos
YTDLP="$INSTALL_DIR/bin/yt-dlp.pyz"
PY=""
for CANDIDATE in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if "$CANDIDATE" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    PY="$CANDIDATE"; break
  fi
done
if [ -z "$PY" ]; then
  echo "ATTENTION : Python 3.10+ introuvable  ->  brew install python, puis relance ce script."
  echo "            En attendant, le yt-dlp de Homebrew sera utilisé."
elif [ -f "$YTDLP" ]; then
  "$PY" "$YTDLP" --update-to nightly || echo "ATTENTION : mise à jour de yt-dlp impossible (réseau ?)"
else
  BASE="https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download"
  TMP="$(mktemp -d)"
  if curl -fsSL -o "$TMP/yt-dlp" "$BASE/yt-dlp" && curl -fsSL -o "$TMP/SHA2-256SUMS" "$BASE/SHA2-256SUMS"; then
    EXPECTED="$(awk '$2 == "yt-dlp" { print $1 }' "$TMP/SHA2-256SUMS")"
    ACTUAL="$(shasum -a 256 "$TMP/yt-dlp" | awk '{ print $1 }')"
    if [ -n "$EXPECTED" ] && [ "$EXPECTED" = "$ACTUAL" ]; then
      mv "$TMP/yt-dlp" "$YTDLP"
      echo "yt-dlp géré installé : $YTDLP"
    else
      echo "ATTENTION : somme de contrôle de yt-dlp invalide, le yt-dlp de Homebrew sera utilisé."
    fi
  else
    echo "ATTENTION : téléchargement de yt-dlp impossible, le yt-dlp de Homebrew sera utilisé."
  fi
  rm -rf "$TMP"
fi

# Commande ytb-dl (terminal, scripts, IA).
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/ytb-dl" <<SH
#!/bin/sh
exec /usr/bin/env python3 "$INSTALL_DIR/ytb_dl.py" "\$@"
SH
chmod +x "$HOME/.local/bin/ytb-dl"
echo "Commande installée : $HOME/.local/bin/ytb-dl"
case ":$ORIG_PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "  (ajoute ~/.local/bin à ton PATH pour taper simplement ytb-dl)" ;;
esac

# Serveur MCP « youtube » pour Claude Code (portée utilisateur : disponible dans tous les projets).
# Pour le retirer : claude mcp remove youtube -s user
if command -v claude >/dev/null 2>&1; then
  claude mcp remove youtube -s user >/dev/null 2>&1 || true
  if claude mcp add -s user youtube -- "$HOME/.local/bin/ytb-dl" mcp >/dev/null; then
    echo "Serveur MCP « youtube » ajouté à Claude Code (outils transcript, download, info, search)."
  else
    echo "ATTENTION : ajout du serveur MCP à Claude Code impossible."
  fi
fi

echo
if [ -n "$PY" ] && [ -f "$YTDLP" ]; then echo "yt-dlp : $("$PY" "$YTDLP" --version) (géré, mis à jour automatiquement, Python : $("$PY" -c 'import sys; print(sys.version.split()[0])'))";
elif command -v yt-dlp >/dev/null 2>&1; then echo "yt-dlp : $(yt-dlp --version) (Homebrew : pense à brew upgrade yt-dlp)";
else echo "ATTENTION : yt-dlp introuvable"; fi
if command -v ffmpeg >/dev/null 2>&1; then echo "ffmpeg : ok"; else echo "ATTENTION : ffmpeg introuvable  ->  brew install ffmpeg"; fi
if command -v deno >/dev/null 2>&1 || command -v node >/dev/null 2>&1; then echo "JavaScript : ok";
else echo "ATTENTION : ni deno ni node  ->  brew install deno (YouTube en a besoin)"; fi
echo
echo "Terminé. Charge maintenant le dossier 'extension' dans chrome://extensions (mode développeur)."
echo "Si l'extension était déjà chargée, clique sur son bouton 'Recharger'."
