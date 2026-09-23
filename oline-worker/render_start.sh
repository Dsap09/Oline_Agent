#!/usr/bin/env bash
# ============================================================
# Oline Worker — bootstrap startup untuk Render Web Service.
# Memastikan node/npm/git + OpenCode CLI tersedia sebelum
# menjalankan uvicorn. Jika sudah ada, dilewati (cepat).
# Jika gagal install, worker tetap jalan (fallback tools dipakai).
# ============================================================
set -u

echo "[bootstrap] Mulai..."

# --- 1. Pastikan node & npm ada (via nvm bila PATH belum punya) ---
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "[bootstrap] node/npm belum ada di PATH, mencoba nvm..."
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  if [ -s "$NVM_DIR/nvm.sh" ]; then
    # shellcheck source=/dev/null
    . "$NVM_DIR/nvm.sh"
  elif [ -s "/usr/local/nvm/nvm.sh" ]; then
    export NVM_DIR="/usr/local/nvm"
    # shellcheck source=/dev/null
    . "$NVM_DIR/nvm.sh"
  fi
  if ! command -v node >/dev/null 2>&1; then
    echo "[bootstrap] nvm tidak tersedia; mencoba apt-get install nodejs..."
    (command -v apt-get >/dev/null 2>&1 && apt-get update -qq && apt-get install -y -qq nodejs npm) >/dev/null 2>&1 || true
  fi
  if ! command -v node >/dev/null 2>&1; then
    echo "[bootstrap] WARNING: node tidak bisa diinstall — jalur OpenCode CLI nonaktif, fallback tools dipakai."
  fi
fi

# --- 2. Pastikan git ada ---
if ! command -v git >/dev/null 2>&1; then
  echo "[bootstrap] git belum ada; mencoba install..."
  (command -v apt-get >/dev/null 2>&1 && apt-get update -qq && apt-get install -y -qq git) >/dev/null 2>&1 || true
  if ! command -v git >/dev/null 2>&1; then
    echo "[bootstrap] WARNING: git tidak tersedia — coding agent (branch/PR) mungkin gagal."
  fi
fi

# --- 3. Pastikan OpenCode CLI ada (install ke prefix lokal, bukan global) ---
export OPENCODE_PREFIX="${OPENCODE_PREFIX:-$HOME/.opencode}"
export OPENCODE_CLI_VERSION="${OPENCODE_CLI_VERSION:-1.18.32}"
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  if ! command -v opencode >/dev/null 2>&1 && [ ! -x "$OPENCODE_PREFIX/node_modules/.bin/opencode" ]; then
    echo "[bootstrap] Menginstall opencode-ai@$OPENCODE_CLI_VERSION ke prefix lokal ($OPENCODE_PREFIX)..."
    npm install --prefix "$OPENCODE_PREFIX" "opencode-ai@$OPENCODE_CLI_VERSION" >/dev/null 2>&1 \
      || echo "[bootstrap] WARNING: gagal install opencode-ai ke prefix lokal."
  fi
  # Tambahkan binary prefix lokal ke PATH agar shutil.which("opencode") ketemu.
  export PATH="$OPENCODE_PREFIX/node_modules/.bin:$PATH"
fi

# --- 4. Jalankan uvicorn ---
echo "[bootstrap] Tool: node=$(command -v node >/dev/null 2>&1 && node --version || echo '-') npm=$(command -v npm >/dev/null 2>&1 && npm --version || echo '-') git=$(command -v git >/dev/null 2>&1 && git --version || echo '-') opencode=$(command -v opencode >/dev/null 2>&1 && opencode --version || echo '-')"
echo "[bootstrap] Memulai uvicorn..."
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"