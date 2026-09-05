#!/usr/bin/env bash
# Translates Home Assistant add-on options into the environment the upstream
# RomM image expects, then hands over to its own entrypoint + s6 tree.
set -euo pipefail

OPTIONS=/data/options.json
UID_ROMM=1000
GID_ROMM=1000

log() { echo "[romm-addon] $*"; }
opt() { jq -r --arg d "${2-}" ".${1} // \$d" "${OPTIONS}"; }

# --- Options -----------------------------------------------------------------
DB_HOST="$(opt db_host)"
DB_PORT="$(opt db_port 3306)"
DB_NAME="$(opt db_name romm)"
DB_USER="$(opt db_user)"
DB_PASSWD="$(opt db_password)"
ROMM_BASE_PATH="$(opt base_path /media/romm)"

for required in DB_HOST DB_USER DB_PASSWD; do
	if [[ -z ${!required} ]]; then
		log "FATAL: '${required,,}' is empty — set it in the add-on Configuration tab."
		log "See the README: the MariaDB add-on needs a database and user created first."
		exit 1
	fi
done

export DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWD ROMM_BASE_PATH
export ROMM_DB_DRIVER=mariadb
export ROMM_PORT=8080

export SCREENSCRAPER_USER="$(opt screenscraper_user)"
export SCREENSCRAPER_PASSWORD="$(opt screenscraper_password)"
export STEAMGRIDDB_API_KEY="$(opt steamgriddb_api_key)"
export RETROACHIEVEMENTS_API_KEY="$(opt retroachievements_api_key)"
export HASHEOUS_API_ENABLED="$(opt hasheous_api_enabled true)"
export SCAN_WORKERS="$(opt scan_workers 1)"
export WEB_SERVER_CONCURRENCY="$(opt web_server_concurrency 2)"
export SCAN_TIMEOUT="$(opt scan_timeout 86400)"
export LOGLEVEL="$(opt log_level INFO)"

# --- Auth secret -------------------------------------------------------------
# Generated once and kept in /data so sessions survive restarts. Upstream wants
# 32 hex bytes; /dev/urandom avoids depending on an openssl binary.
SECRET_FILE=/data/.auth_secret
if [[ ! -s ${SECRET_FILE} ]]; then
	log "Generating ROMM_AUTH_SECRET_KEY (first run)..."
	head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' >"${SECRET_FILE}"
	chmod 600 "${SECRET_FILE}"
fi
export ROMM_AUTH_SECRET_KEY="$(cat "${SECRET_FILE}")"

# --- Library layout ----------------------------------------------------------
# All four live under one mount: RomM hardlinks between them (os.link), and a
# cross-device link fails with EXDEV. Keeping them together is also what makes
# a later move to an external disk a mount change and nothing more.
for dir in library resources assets config; do
	mkdir -p "${ROMM_BASE_PATH}/${dir}"
done
chown -R "${UID_ROMM}:${GID_ROMM}" "${ROMM_BASE_PATH}" 2>/dev/null || \
	log "WARN: could not chown ${ROMM_BASE_PATH} — uploads may fail if permissions are wrong."

# --- Valkey working directory ------------------------------------------------
# /redis-data is declared VOLUME upstream, so Docker mounts it and it cannot be
# replaced with a symlink from inside the container ("Resource busy"). It is left
# as-is: Valkey holds task queues and cached results, which RomM rebuilds, so
# losing it on a container recreate costs an interrupted background task at
# worst. Only chown it, so the romm user can write.
chown -R "${UID_ROMM}:${GID_ROMM}" /redis-data 2>/dev/null || \
	log "WARN: could not chown /redis-data — Valkey may fail to write its snapshot."

log "Library:  ${ROMM_BASE_PATH}"
log "Database: ${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
log "Web UI:   http://<home-assistant>:8080"
log "Handing over to the upstream entrypoint..."

exec /docker-entrypoint.sh "$@"
