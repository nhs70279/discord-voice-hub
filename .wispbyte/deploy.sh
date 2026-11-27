#!/bin/sh
# Wispbyte runs this before launching the Python bot. Only an explicit marker
# permits the one-time JS provider install; errors must never prevent Discord
# and Thai TTS from starting.
if [ -f /home/container/data/youtube-pot.enabled ]; then
    python3 /home/container/scripts/provision_youtube_pot.py || echo "[Lilin] PO token setup skipped; bot will start normally"
fi
exit 0
