#!/bin/sh
# The Fly Volume is initially root-owned. Fix only its mount point, then run
# the Python application as an unprivileged user.
set -eu

install -d -m 0700 -o voicehub -g voicehub /data
chown voicehub:voicehub /data
chmod 0700 /data
umask 077

exec gosu voicehub "$@"
