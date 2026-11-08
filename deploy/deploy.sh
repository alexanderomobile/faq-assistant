#!/usr/bin/env bash
# Выкладка FAQ-ассистента в LXC 171 на pve, рядом с сайтом ai.optimum-ltd.com. Запускать из Git Bash:
#   bash deploy/deploy.sh                       # обновить код и базу знаний, прогнать тесты, перезапустить
#   bash deploy/deploy.sh --secrets path/.env   # первый запуск: взять из файла строку OPENAI_API_KEY=
# Ключ идёт потоком прямо в контейнер и в выводе не печатается. Соль для кода диалога (USER_HASH_SALT)
# создаётся на сервере один раз и при обновлениях не меняется. Индекс собирается заранее
# (python -m backend.build_index) и едет вместе с кодом: на сервере эмбеддинги базы не пересчитываются.
# Порядок: тесты гоняются на отдельной копии, и только если они прошли, код заменяется и служба перезапускается.
set -euo pipefail

HOST=pve-lan
CT=171
APP=/opt/faq-assistant
STAGE=/opt/faq-assistant.stage
SECRETS=""
if [ "${1:-}" = "--secrets" ]; then
  SECRETS="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"   # до cd ниже: относительный путь не потеряется
  grep -q '^OPENAI_API_KEY=.' "$SECRETS" || { echo "в $SECRETS нет строки OPENAI_API_KEY="; exit 1; }
fi
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
TGZ="$TMP/faq-assistant.tgz"

cd "$HERE"
test -f data/index/faiss.index || { echo 'нет data/index: сначала python -m backend.build_index'; exit 1; }
tar czf "$TGZ" --owner=0 --group=0 --exclude=__pycache__ \
    backend data/faqs.json data/docs data/index requirements.txt requirements-dev.txt tests deploy
LOCAL_SUM="$(sha256sum "$TGZ" | cut -d' ' -f1)"
scp -q "$TGZ" "$HOST:/tmp/faq-assistant.tgz"
ssh "$HOST" "pct push $CT /tmp/faq-assistant.tgz /tmp/faq-assistant.tgz && rm -f /tmp/faq-assistant.tgz"
rm -rf "$TMP"

if [ -n "$SECRETS" ]; then
  grep -E '^OPENAI_API_KEY=' "$SECRETS" | tr -d '\r' | \
    ssh "$HOST" "pct exec $CT -- sh -c 'umask 077; cat > /root/.faq-openai-key'"
fi

ssh "$HOST" "pct exec $CT -- bash -s" <<EOF
set -euo pipefail
UV=/usr/local/bin/uv   # pct exec запускает голый процесс: PATH без /usr/local/bin
# Ключ из --secrets не должен остаться в /root, чем бы ни кончился запуск.
trap 'rm -f /root/.faq-openai-key /tmp/faq-assistant.tgz' EXIT

id faq >/dev/null 2>&1 || useradd --system --home-dir /var/lib/faq-assistant --shell /usr/sbin/nologin faq
if [ -s /root/.faq-openai-key ]; then
  umask 077
  SALT="\$(grep '^USER_HASH_SALT=' /etc/faq-assistant.env 2>/dev/null || true)"
  [ -n "\$SALT" ] || SALT="USER_HASH_SALT=\$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  { cat /root/.faq-openai-key; echo "\$SALT"; } > /etc/faq-assistant.env.new
  mv /etc/faq-assistant.env.new /etc/faq-assistant.env
  umask 022
fi
test -f /etc/faq-assistant.env || { echo 'нет /etc/faq-assistant.env: запустите с --secrets'; exit 1; }
chown root:faq /etc/faq-assistant.env && chmod 640 /etc/faq-assistant.env

test "\$(sha256sum /tmp/faq-assistant.tgz | cut -d' ' -f1)" = "$LOCAL_SUM" || { echo 'архив доехал битым'; exit 1; }
test -x \$UV || { echo 'нет uv: его ставит выкладка сайта (deploy/deploy.sh в папке site)'; exit 1; }

# 1. Тесты на отдельной копии: рабочий код и его окружение пока не тронуты.
rm -rf $STAGE && install -d -m 755 $STAGE
tar xzf /tmp/faq-assistant.tgz --no-same-owner -C $STAGE
\$UV venv --quiet --python /usr/bin/python3 $STAGE/.venv
\$UV pip install --quiet --python $STAGE/.venv/bin/python -r $STAGE/requirements.txt -r $STAGE/requirements-dev.txt
cd /tmp
if ! PYTHONDONTWRITEBYTECODE=1 $STAGE/.venv/bin/python -m pytest -q -p no:cacheprovider $STAGE/tests > /tmp/faq-tests.log 2>&1; then
  tail -40 /tmp/faq-tests.log; rm -rf $STAGE; echo 'тесты не прошли: рабочий код не тронут'; exit 1
fi
tail -1 /tmp/faq-tests.log
rm -rf $STAGE

# 2. Тесты прошли: меняем код и окружение, перезапускаем службу.
install -d -o root -g root -m 755 $APP
install -d -o faq -g faq -m 750 /var/lib/faq-assistant
find $APP -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} +
tar xzf /tmp/faq-assistant.tgz --no-same-owner -C $APP
chown -R root:root $APP
[ -x $APP/.venv/bin/python ] || \$UV venv --quiet --python /usr/bin/python3 $APP/.venv
\$UV pip install --quiet --python $APP/.venv/bin/python -r $APP/requirements.txt

install -m 644 $APP/deploy/faq-assistant.service /etc/systemd/system/faq-assistant.service
install -m 644 $APP/deploy/faq-assistant-cleanup.service /etc/systemd/system/faq-assistant-cleanup.service
install -m 644 $APP/deploy/faq-assistant-cleanup.timer /etc/systemd/system/faq-assistant-cleanup.timer
systemctl daemon-reload
systemctl enable --quiet faq-assistant
systemctl enable --quiet --now faq-assistant-cleanup.timer
systemctl restart faq-assistant
ok=0
for i in \$(seq 1 30); do
  if curl -fsS http://127.0.0.1:8301/health 2>/dev/null; then echo; echo "faq-assistant: ok"; ok=1; break; fi
  sleep 1
done
[ \$ok = 1 ] || { echo "faq-assistant: не поднялся"; journalctl -u faq-assistant -n 40 --no-pager; exit 1; }

# 3. Сайт включает чат, когда знает адрес помощника. Если сайт уже стоит, а строки нет — дописать и перезапустить.
if [ -f /etc/brand-site.env ] && ! grep -q '^FAQ_BACKEND_URL=' /etc/brand-site.env; then
  echo 'FAQ_BACKEND_URL=http://127.0.0.1:8301' >> /etc/brand-site.env
  systemctl restart brand-site && echo "brand-site: чат включён"
fi
EOF
