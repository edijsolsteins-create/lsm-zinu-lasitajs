#!/data/data/com.termux/files/usr/bin/sh
# Start Ziņu lasītājs on an Android phone (Termux). Opens Chrome by itself.
# For a one-tap home-screen icon: install Termux:Widget and run
#   mkdir -p ~/.shortcuts && cp ~/lsm-zinu-lasitajs/start-android.sh ~/.shortcuts/Zinas.sh
cd "$HOME/lsm-zinu-lasitajs" || exit 1
termux-wake-lock 2>/dev/null      # keep running while Chrome is in front
git pull --quiet 2>/dev/null      # pick up updates when online
exec python server.py
