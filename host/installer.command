#!/bin/bash
# Installation (une seule fois par ordinateur) : enregistre l'hôte natif auprès de Chrome.
# Double-clic dans le Finder, ou : bash host/installer.command
#
# Prérequis : Homebrew avec yt-dlp et ffmpeg  ->  brew install yt-dlp ffmpeg

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_NAME="com.oksitaine.ytb_dl"
EXTENSION_ID="lpccinmpoibiiophngcnjbhmooccncdg"

# Le script est copié hors des dossiers protégés par macOS (Bureau, Documents...) :
# Chrome n'a pas forcément le droit d'y lire un programme, ~/Library l'autorise toujours.
INSTALL_DIR="$HOME/Library/Application Support/YouTubeDownloadButton"
HOST_SCRIPT="$INSTALL_DIR/ytb_dl_host.py"
mkdir -p "$INSTALL_DIR"
cp "$DIR/ytb_dl_host.py" "$HOST_SCRIPT"
chmod +x "$HOST_SCRIPT"
echo "Hôte natif copié : $HOST_SCRIPT"

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

echo
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if command -v yt-dlp >/dev/null 2>&1; then echo "yt-dlp : $(yt-dlp --version)"; else echo "ATTENTION : yt-dlp introuvable  ->  brew install yt-dlp"; fi
if command -v ffmpeg >/dev/null 2>&1; then echo "ffmpeg : ok"; else echo "ATTENTION : ffmpeg introuvable  ->  brew install ffmpeg"; fi
echo
echo "Terminé. Charge maintenant le dossier 'extension' dans chrome://extensions (mode développeur)."
echo "Si l'extension était déjà chargée, clique sur son bouton 'Recharger'."
