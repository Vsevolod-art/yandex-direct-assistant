#!/bin/bash
# Установка ассистента на VPS с Ubuntu/Debian.
# Запускается от root одной командой. Повторный запуск безопасен —
# скрипт обновляет код и настройки, не трогая .env с токенами.
set -euo pipefail

REPO="${REPO:-https://github.com/Vsevolod-art/yandex-direct-assistant.git}"
DIR="/opt/direct-assistant"
LOG="/var/log/direct-assistant.log"

echo
echo "=== Установка ассистента чистки площадок ==="
echo

if [ "$(id -u)" -ne 0 ]; then
  echo "Запусти от root:  sudo bash $0"
  exit 1
fi

# --- Системные пакеты ---
echo "[1/6] Ставлю системные пакеты..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git tzdata >/dev/null

# Часовой пояс сервера — московский, чтобы расписание читалось без пересчёта.
timedatectl set-timezone Europe/Moscow 2>/dev/null || true
echo "      Время сервера: $(date '+%Y-%m-%d %H:%M:%S %Z')"

# --- Код ---
if [ -d "$DIR/.git" ]; then
  echo "[2/6] Обновляю код..."
  git -C "$DIR" pull --quiet
else
  echo "[2/6] Скачиваю код..."
  rm -rf "$DIR"
  mkdir -p "$(dirname "$DIR")"
  git clone --quiet "$REPO" "$DIR"
fi

cd "$DIR"

# --- Окружение ---
echo "[3/6] Создаю окружение..."
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

if ! ./.venv/bin/python -c "import requests, yaml, dotenv" 2>/dev/null; then
  echo "ОШИБКА: зависимости не установились. Проверь интернет на сервере."
  exit 1
fi
echo "      Зависимости готовы."

# --- Файл с токенами ---
echo "[4/6] Готовлю файл настроек..."
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env          # читать может только root
  echo "      Создан .env — токены впишешь следующим шагом."
else
  chmod 600 .env
  echo "      .env уже есть, не трогаю."
fi

# --- Обёртка ---
echo "[5/6] Настраиваю запуск..."
cat > "$DIR/run.sh" <<'WRAPPER'
#!/bin/bash
cd /opt/direct-assistant
echo "=== Запуск $(date '+%Y-%m-%d %H:%M:%S') ==="
./.venv/bin/python src/main.py
echo "=== Код завершения: $? ==="
WRAPPER
chmod +x "$DIR/run.sh"
touch "$LOG"

# --- Расписание ---
echo "[6/6] Ставлю расписание..."
cat > /etc/cron.d/direct-assistant <<CRON
# Ассистент чистки площадок РСЯ — каждый понедельник в 9:00 по Москве.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 9 * * 1 root $DIR/run.sh >> $LOG 2>&1
CRON
chmod 644 /etc/cron.d/direct-assistant
systemctl restart cron 2>/dev/null || service cron restart 2>/dev/null || true

cat <<MSG

=== Готово ===

Код:        $DIR
Настройки:  $DIR/.env
Лог:        $LOG
Расписание: каждый понедельник в 9:00 по Москве

Что дальше:

  1. Впиши токены:
       nano $DIR/.env

  2. Укажи целевой CPA:
       nano $DIR/config.yaml
     (найди строку target_cpa: null и замени null на свою цифру)

  3. Проверь работу прямо сейчас:
       $DIR/run.sh

В nano: сохранить — Ctrl+O затем Enter, выйти — Ctrl+X.

MSG
