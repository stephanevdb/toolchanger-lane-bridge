#!/usr/bin/env bash
# Install / update lanebridge. Safe to run more than once.
# Uses sudo only to install and enable the systemd unit.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="${HOME}/lanebridge-env"
DATA="${HOME}/printer_data"
CONF_DIR="${DATA}/config"
CONF="${CONF_DIR}/lanebridge.conf"
ASVC="${DATA}/moonraker.asvc"
UNIT=/etc/systemd/system/lanebridge.service

if [[ $EUID -eq 0 ]]; then
    echo "Run as the printer user, not root." >&2
    exit 1
fi
if [[ "${REPO}" != "${HOME}/toolchanger-lane-bridge" ]]; then
    echo "Note: the unit expects the repo at ~/toolchanger-lane-bridge (it is at ${REPO})." >&2
fi

backup_dir=""
backup() {  # backup <file>: copy into one backup dir per run
    if [[ -z "${backup_dir}" ]]; then
        backup_dir="${CONF_DIR}/backup-lanebridge-$(date +%Y%m%d-%H%M%S)"
        mkdir -p "${backup_dir}"
    fi
    [[ -e "$1" ]] && cp -a "$1" "${backup_dir}/"
    return 0
}

echo "== Python environment (${ENV_DIR})"
if [[ ! -x "${ENV_DIR}/bin/python" ]]; then
    python3 -m venv "${ENV_DIR}"
fi
"${ENV_DIR}/bin/pip" install -q --disable-pip-version-check -r "${REPO}/requirements.txt"

echo "== Config (${CONF})"
if [[ ! -e "${CONF}" ]]; then
    backup "${CONF_DIR}/moonraker.conf"
    cp "${REPO}/lanebridge.conf.example" "${CONF}"
    echo "   created from lanebridge.conf.example"
else
    echo "   exists, left unchanged"
fi

echo "== Moonraker service list (${ASVC})"
if ! grep -qx "lanebridge" "${ASVC}" 2>/dev/null; then
    backup "${ASVC}"
    # the file may not end with a newline
    if [[ -s "${ASVC}" && -n "$(tail -c1 "${ASVC}")" ]]; then
        echo >> "${ASVC}"
    fi
    echo "lanebridge" >> "${ASVC}"
    echo "   added lanebridge (takes effect after the next Moonraker restart)"
else
    echo "   already present"
fi

echo "== systemd unit (${UNIT})"
tmp="$(mktemp)"
sed -e "s|%USER%|${USER}|g" -e "s|%HOME%|${HOME}|g" "${REPO}/lanebridge.service" > "${tmp}"
if ! cmp -s "${tmp}" "${UNIT}"; then
    sudo install -m 644 "${tmp}" "${UNIT}"
    sudo systemctl daemon-reload
    echo "   installed"
else
    echo "   up to date"
fi
rm -f "${tmp}"
sudo systemctl enable lanebridge.service
sudo systemctl restart lanebridge.service
sleep 2
systemctl --no-pager --lines=5 status lanebridge.service || true

[[ -n "${backup_dir}" ]] && echo "Backups: ${backup_dir}"

if ! grep -q "^\[update_manager lanebridge\]" "${CONF_DIR}/moonraker.conf" 2>/dev/null; then
    cat <<EOF

To let Moonraker's update manager track this repo, add to moonraker.conf
once the repo has a git remote (replace <origin>):

[update_manager lanebridge]
type: git_repo
path: ~/toolchanger-lane-bridge
origin: <origin>
primary_branch: main
virtualenv: ~/lanebridge-env
requirements: requirements.txt
managed_services: lanebridge
EOF
fi
