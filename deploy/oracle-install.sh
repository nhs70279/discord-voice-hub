#!/usr/bin/env bash
# Run on an Oracle Cloud Ubuntu 24.04 ARM64 VM:
#   sudo bash deploy/oracle-install.sh
# Does not start the bot until .env has been configured.
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo 'Please run: sudo bash deploy/oracle-install.sh' >&2
  exit 1
fi
if [[ $(uname -m) != aarch64 ]]; then
  echo 'This installer targets Oracle Ampere A1 (ARM64/aarch64) only.' >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$(cd -- "$script_dir/.." && pwd)"
destination=/opt/voice-hub

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl ffmpeg python3 python3-venv python3-pip \
  rsync sqlite3 unzip

if ! id voicehub >/dev/null 2>&1; then
  useradd --system --user-group --create-home \
    --home-dir /var/lib/voice-hub --shell /usr/sbin/nologin voicehub
fi
install -d -o voicehub -g voicehub -m 0750 /var/lib/voice-hub
install -d -o root -g root -m 0755 "$destination"

if [[ "$source_dir" != "$destination" ]]; then
  rsync --archive \
    --exclude='.env' --exclude='.venv/' --exclude='__pycache__/' \
    --exclude='*.pyc' --exclude='*.sqlite3*' --exclude='*.zip' \
    "$source_dir/" "$destination/"
fi
chown -R root:root "$destination"

if [[ ! -e "$destination/.env" ]]; then
  install -o root -g voicehub -m 0640 \
    "$destination/.env.oracle.example" "$destination/.env"
  echo 'Created /opt/voice-hub/.env (edit secrets and domain before starting).'
fi
chown root:voicehub "$destination/.env"
chmod 0640 "$destination/.env"

if [[ ! -x /usr/local/bin/deno ]]; then
  # Official Deno Linux ARM64 release, verified with its published SHA-256.
  temp_dir="$(mktemp -d)"
  trap 'rm -rf -- "$temp_dir"' EXIT
  deno_zip=deno-aarch64-unknown-linux-gnu.zip
  download_url="https://github.com/denoland/deno/releases/latest/download/$deno_zip"
  curl --fail --location --retry 3 --silent --show-error \
    "$download_url" --output "$temp_dir/$deno_zip"
  curl --fail --location --retry 3 --silent --show-error \
    "$download_url.sha256sum" --output "$temp_dir/$deno_zip.sha256sum"
  expected="$(awk 'NR==1 { print $1 }' "$temp_dir/$deno_zip.sha256sum")"
  if [[ ! $expected =~ ^[[:xdigit:]]{64}$ ]]; then
    echo 'Invalid Deno SHA-256 checksum file' >&2
    exit 1
  fi
  (cd "$temp_dir" && printf '%s  %s\n' "$expected" "$deno_zip" | sha256sum --check --status)
  unzip -q "$temp_dir/$deno_zip" -d "$temp_dir/extracted"
  install -o root -g root -m 0755 "$temp_dir/extracted/deno" /usr/local/bin/deno
fi

python3 -m venv "$destination/.venv"
"$destination/.venv/bin/python" -m pip install --upgrade pip
"$destination/.venv/bin/python" -m pip install -r "$destination/requirements.txt"

install -o root -g root -m 0644 \
  "$destination/deploy/voicehub.service" /etc/systemd/system/voicehub.service
systemctl daemon-reload
systemctl enable voicehub.service

echo
echo 'Installation files are ready.'
echo '1. sudoedit /opt/voice-hub/.env'
echo '2. sudo systemctl start voicehub'
echo '3. sudo systemctl status voicehub --no-pager'
echo '4. Follow docs/ORACLE_CLOUDFLARE_TH.md to create Cloudflare Tunnel.'
