#!/usr/bin/env bash
# Install Ollama from the official release tarball, deliberately not via
# https://ollama.com/install.sh.
#
# Why not the official installer: it probes for an NVIDIA GPU and, on finding
# one, may offer to install a CUDA driver. This host has a Quadro M3000M with no
# driver installed and a working Intel default — a driver install is not wanted
# here. Installing the tarball by hand does exactly one thing.
#
# Ollama is needed by prompt 04 (DocMind's local inference) and by the local-first
# mode described in ARCHITECTURE.md. It is NOT needed by MetaMCP.
#
# Note on pipefail: do not pipe `tar -t` into `head`. `head` closes the pipe
# after five lines, `tar` dies of SIGPIPE, and `set -o pipefail` turns that into
# an aborted install that still looks like it succeeded.
set -euo pipefail

ARCH=amd64
URL="https://ollama.com/download/ollama-linux-${ARCH}.tar.zst"
CACHE=/var/tmp/ollama-linux-${ARCH}.tar.zst

if [ -s "$CACHE" ] && zstd -t "$CACHE" 2>/dev/null; then
    echo "== reusing cached archive $CACHE"
else
    echo "== downloading $URL"
    curl -fSL --progress-bar "$URL" -o "$CACHE.part"
    mv "$CACHE.part" "$CACHE"
fi
ls -lh "$CACHE"
zstd -t "$CACHE"

echo "== archive contents (top level)"
tar --use-compress-program=unzstd -tf "$CACHE" > /var/tmp/ollama-contents.txt
echo "   $(wc -l < /var/tmp/ollama-contents.txt) entries; first few:"
grep -E '^bin/|^lib/ollama/ollama$' /var/tmp/ollama-contents.txt | head -3 || true

echo "== installing into /usr/local"
sudo install -o0 -g0 -m755 -d /usr/local/lib/ollama
sudo tar --use-compress-program=unzstd -xf "$CACHE" -C /usr/local

echo "== service account"
id ollama >/dev/null 2>&1 || sudo useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama
# Create the model store and then fix up its parent explicitly.
#
# `install -d -o ollama -g ollama -m755 /usr/share/ollama/.ollama/models` applies
# the ownership to the final directory only when it has to create intermediate
# ones: `.ollama` ends up root:root, and ollama then fails at startup with
#   Error: open /usr/share/ollama/.ollama/id_ed25519: permission denied
# leaving the service in a restart loop. Setting the owner on the parent too is
# what makes this work from a clean machine.
sudo install -o ollama -g ollama -m755 -d /usr/share/ollama/.ollama/models
sudo chown ollama:ollama /usr/share/ollama/.ollama
sudo chmod 755 /usr/share/ollama/.ollama

echo "== systemd unit"
sudo tee /etc/systemd/system/ollama.service >/dev/null <<'UNIT'
[Unit]
Description=Ollama LLM server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ollama
Group=ollama
ExecStart=/usr/local/bin/ollama serve
Restart=on-failure
RestartSec=3
# Local inference for the demo only: no GPU driver on this host, CPU execution.
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_MODELS=/usr/share/ollama/.ollama/models"
Environment="OLLAMA_KEEP_ALIVE=10m"

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now ollama
sleep 6
echo "== service: $(systemctl is-active ollama) / $(systemctl is-enabled ollama)"
/usr/local/bin/ollama --version
curl -s -o /dev/null -w "== http://127.0.0.1:11434 -> %{http_code}\n" --max-time 10 http://127.0.0.1:11434/
