#!/usr/bin/env bash
# Set up a Linux VM to run the example agents on Gemini: Docker, a local
# Langfuse next to this repository, the Python environment and .env.
# Written for RHEL 10 (dnf); also handles Debian/Ubuntu (apt).
#
# Run from a clone of this repository:
#     GEMINI_AUTH=vertex bash examples/vm_setup.sh                       # VM service account
#     GEMINI_AUTH=apikey GOOGLE_API_KEY=... bash examples/vm_setup.sh    # Gemini API key
#
# Safe to re-run: existing clones, .venv and .env are left alone. Makes no model calls.
set -euo pipefail

GEMINI_AUTH="${GEMINI_AUTH:-vertex}"              # vertex or apikey
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANGFUSE_DIR="$(dirname "$REPO")/langfuse"

# --- 1. System packages and Docker --------------------------------------------
if command -v dnf >/dev/null; then
  sudo dnf -y install git python3 python3-pip dnf-plugins-core     # curl ships with RHEL
  if ! command -v docker >/dev/null; then
    sudo dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
    sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
  fi
else
  sudo apt-get update -y
  sudo apt-get install -y git curl ca-certificates
  command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh
fi
sudo systemctl enable --now docker
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"

# --- 2. Langfuse (UI :3000, ClickHouse :8123) ---------------------------------
[ -d "$LANGFUSE_DIR" ] || git clone --depth 1 https://github.com/langfuse/langfuse.git "$LANGFUSE_DIR"
cp "$REPO/docs/langfuse.docker-compose.override.example.yml" "$LANGFUSE_DIR/docker-compose.override.yml"
(cd "$LANGFUSE_DIR" && $DOCKER compose up -d)

# --- 3. Python 3.12 environment (only what the examples and the report need) ---
cd "$REPO"
PKGS=$(grep -E '^(google-adk|google-genai|langfuse|openinference-instrumentation-google-adk|python-dotenv|clickhouse-connect)==' requirements.txt)
if [ ! -d .venv ]; then
  if command -v python3.12 >/dev/null && python3.12 -m venv .venv; then
    .venv/bin/pip install --quiet $PKGS
  else                                            # no usable Python 3.12: let uv fetch one
    rm -rf .venv
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python $PKGS
  fi
fi

# --- 4. .env for Gemini -------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  sed -i "s|^ASHWA_LLM_PROVIDER=.*|ASHWA_LLM_PROVIDER=gemini|; s|^ASHWA_MODEL=.*|ASHWA_MODEL=$GEMINI_MODEL|" .env
  if [ "$GEMINI_AUTH" = "vertex" ]; then
    PROJECT="${GOOGLE_CLOUD_PROJECT:-$(curl -sf -H 'Metadata-Flavor: Google' \
      http://metadata.google.internal/computeMetadata/v1/project/project-id || true)}"
    [ -n "$PROJECT" ] || { rm .env; echo "Set GOOGLE_CLOUD_PROJECT and re-run."; exit 1; }
    sed -i "s|^GOOGLE_GENAI_USE_VERTEXAI=.*|GOOGLE_GENAI_USE_VERTEXAI=TRUE|" .env
    printf 'GOOGLE_CLOUD_PROJECT=%s\nGOOGLE_CLOUD_LOCATION=%s\n' "$PROJECT" "$GOOGLE_CLOUD_LOCATION" >> .env
  else
    [ -n "${GOOGLE_API_KEY:-}" ] || { rm .env; echo "Set GOOGLE_API_KEY and re-run."; exit 1; }
    sed -i "s|^GOOGLE_API_KEY=.*|GOOGLE_API_KEY=$GOOGLE_API_KEY|" .env
  fi
fi

# --- 5. Wait for Langfuse and ClickHouse --------------------------------------
echo "Waiting for Langfuse (two to three minutes on first start)..."
up() { curl -sf http://localhost:3000/api/public/health >/dev/null && curl -sf http://localhost:8123/ping >/dev/null; }
for _ in $(seq 60); do up && break; sleep 5; done
up || { echo "Langfuse or ClickHouse did not come up: check '$DOCKER compose ps' in $LANGFUSE_DIR"; exit 1; }

cat <<MSG

Setup complete. Next, from $REPO (each run calls Gemini):
  .venv/bin/python examples/trip_planner/run.py --limit 2          # smoke test
  .venv/bin/python examples/trip_planner/run.py --variants a b
  .venv/bin/python examples/helpdesk_team/run.py --variants a b
Each run prints the ashwa_report.py commands that write its HTML reports.
MSG
