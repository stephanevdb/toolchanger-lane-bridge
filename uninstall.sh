#!/usr/bin/env bash
# Remove lanebridge: service, unit, asvc entry, update_manager block and the
# lane_data keys it wrote. Leaves the repo, venv and lanebridge.conf in place.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="${HOME}/lanebridge-env"
DATA="${HOME}/printer_data"
CONF_DIR="${DATA}/config"
ASVC="${DATA}/moonraker.asvc"
MR_CONF="${CONF_DIR}/moonraker.conf"
UNIT=/etc/systemd/system/lanebridge.service

if [[ $EUID -eq 0 ]]; then
    echo "Run as the printer user, not root." >&2
    exit 1
fi

backup_dir="${CONF_DIR}/backup-lanebridge-$(date +%Y%m%d-%H%M%S)"

echo "== Stopping service"
if systemctl list-unit-files lanebridge.service >/dev/null 2>&1 && [[ -e "${UNIT}" ]]; then
    sudo systemctl disable --now lanebridge.service || true
fi

echo "== Removing lane_data keys"
if [[ -x "${ENV_DIR}/bin/python" ]]; then
    (cd "${REPO}" && "${ENV_DIR}/bin/python" -m lanebridge -c "${CONF_DIR}/lanebridge.conf" --purge) \
        || echo "   could not reach Moonraker; remove lane_data keys later with: python -m lanebridge --purge"
else
    echo "   venv missing; skipped"
fi

echo "== Removing systemd unit"
if [[ -e "${UNIT}" ]]; then
    sudo rm -f "${UNIT}"
    sudo systemctl daemon-reload
fi

echo "== Removing lanebridge from ${ASVC}"
if grep -qx "lanebridge" "${ASVC}" 2>/dev/null; then
    mkdir -p "${backup_dir}"
    cp -a "${ASVC}" "${backup_dir}/"
    sed -i '/^lanebridge$/d' "${ASVC}"
fi

echo "== Removing [update_manager lanebridge] from moonraker.conf"
if grep -q "^\[update_manager lanebridge\]" "${MR_CONF}" 2>/dev/null; then
    mkdir -p "${backup_dir}"
    cp -a "${MR_CONF}" "${backup_dir}/"
    awk '
        /^\[update_manager lanebridge\][[:space:]]*$/ { skip = 1; next }
        /^\[/ { skip = 0 }
        !skip
    ' "${backup_dir}/moonraker.conf" > "${MR_CONF}"
    echo "   removed (restart Moonraker when idle to apply)"
fi

[[ -d "${backup_dir}" ]] && echo "Backups: ${backup_dir}"
echo "Done. To remove everything else: rm -rf ~/toolchanger-lane-bridge ${ENV_DIR} ${CONF_DIR}/lanebridge.conf"
