#!/usr/bin/env bash
# Install Lilin natively on Google Cloud e2-micro Ubuntu 24.04 x86_64.
# Run from a checkout: sudo bash deploy/gcp-install.sh
# This script does NOT start the bot until credentials are configured.
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo 'Run: sudo bash deploy/gcp-install.sh' >&2
  exit 1
fi

if [[ $(uname -m) != x86_64 ]]; then
  echo 'Google Cloud e2-micro installer requires x86_64 (amd64).' >&2
  exit 1
fi

if [[ ! -f /etc/os-release ]] || ! grep -q '^ID=ubuntu$' /etc/os-release; then
  echo 'This installer targets Ubuntu 24.04 LTS.' >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$(cd -- "$script_dir/.." && pwd)"
destination=/opt/voice-hub

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl ffmpeg git python3 python3-venv python3-pip \
  rsync sqlite3 unzip

# e2-micro has only 1 GiB RAM. Swap helps during wheel installation and
# bursts of Python/FFmpeg memory use. Standard PD usage counts toward 30 GiB.
if ! swapon --show=NAME --noheadings | grep -qx '/swapfile'; then
  if [[ ! -e /swapfile ]]; then
    if [[ $(df -Pk / | awk 'NR == 2 {print $4}') -lt 3145728 ]]; then
      echo 'At least 3 GiB of free filesystem space is needed for 2 GiB swap.' >&2
      exit 1
    fi
    fallocate -l 2G /swapfile
    chmod 0600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
fi
if ! grep -Eq '^[^#]*[[:space:]]/swapfile[[:space:]]' /etc/fstab; then
  printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if ! id voicehub >/dev/null 2>&1; then
  useradd --system --user-group --create-home \
    --home-dir /var/lib/voice-hub --shell /usr/sbin/nologin voicehub
fi
install -d -o voicehub -g voicehub -m 0700 /var/lib/voice-hub
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
    "$destination/.env.gcp.example" "$destination/.env"
  echo 'Created /opt/voice-hub/.env; configure secrets before starting the service.'
fi
chown root:voicehub "$destination/.env"
chmod 0640 "$destination/.env"

if [[ ! -x /usr/local/bin/deno ]]; then
  # Download the official x86_64 release and verify its published SHA-256.
  temp_dir="$(mktemp -d)"
  trap 'rm -rf -- "$temp_dir"' EXIT
  deno_zip=deno-x86_64-unknown-linux-gnu.zip
  download_url="https://github.com/denoland/deno/releases/latest/download/$deno_zip"
  curl --fail --location --retry 3 --silent --show-error \
    "$download_url" --output "$temp_dir/$deno_zip"
  curl --fail --location --retry 3 --silent --show-error \
    "$download_url.sha256sum" --output "$temp_dir/$deno_zip.sha256sum"
  expected="$(awk 'NR==1 { print $1 }' "$temp_dir/$deno_zip.sha256sum")"
  if [[ ! $expected =~ ^[[:xdigit:]]{64}$ ]]; then
    echo 'Invalid Deno SHA-256 checksum' >&2
    exit 1
  fi
  (cd "$temp_dir" && printf '%s  %s\n' "$expected" "$deno_zip" | sha256sum --check --status)
  mkdir -p "$temp_dir/extracted"
  unzip -q "$temp_dir/$deno_zip" -d "$temp_dir/extracted"
  install -o root -g root -m 0755 "$temp_dir/extracted/deno" /usr/local/bin/deno
fi

python3 -m venv "$destination/.venv"
"$destination/.venv/bin/python" -m pip install --no-cache-dir -r "$destination/requirements.txt"

install -o root -g root -m 0644 \
  "$destination/deploy/voicehub.service" /etc/systemd/system/voicehub.service
systemctl daemon-reload
systemctl enable voicehub.service

echo
echo 'Installed. The bot has NOT been started.'
echo '1. Set up Tailscale Funnel as described in docs/GOOGLE_CLOUD_E2_MICRO_JA.md.'
echo '2. sudoedit /opt/voice-hub/.env'
echo '3. sudo systemctl start voicehub'
echo '4. sudo systemctl status voicehub --no-pager'
