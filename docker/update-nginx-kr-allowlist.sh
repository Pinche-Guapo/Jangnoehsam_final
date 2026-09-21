#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/docker/nginx-geo"
KR_OUT_FILE="${OUT_DIR}/allow_kr.conf"
ICLOUD_OUT_FILE="${OUT_DIR}/allow_icloud_private_relay_kr.conf"
MANUAL_OUT_FILE="${OUT_DIR}/allow_manual.conf"
KR_TMP_FILE="$(mktemp)"
ICLOUD_TMP_FILE="$(mktemp)"
KR_SOURCE_URL="https://www.ipdeny.com/ipblocks/data/countries/kr.zone"
ICLOUD_SOURCE_URL="https://mask-api.icloud.com/egress-ip-ranges.csv"

mkdir -p "${OUT_DIR}"

curl -fsSL "${KR_SOURCE_URL}" -o "${KR_TMP_FILE}"
awk 'NF>0 && $0 !~ /^#/ {print $0 " 1;"}' "${KR_TMP_FILE}" > "${KR_OUT_FILE}"

curl -fsSL "${ICLOUD_SOURCE_URL}" -o "${ICLOUD_TMP_FILE}"
awk -F',' '$2=="KR" {print $1 " 1;"}' "${ICLOUD_TMP_FILE}" > "${ICLOUD_OUT_FILE}"

if [[ ! -f "${MANUAL_OUT_FILE}" ]]; then
  cat > "${MANUAL_OUT_FILE}" <<'EOF'
# Manual temporary allow entries (CIDR format).
# Keep entries minimal and review periodically.
EOF
fi

rm -f "${KR_TMP_FILE}" "${ICLOUD_TMP_FILE}"

kr_count="$(wc -l < "${KR_OUT_FILE}" | tr -d ' ')"
icloud_count="$(wc -l < "${ICLOUD_OUT_FILE}" | tr -d ' ')"
echo "Updated ${KR_OUT_FILE} (${kr_count} CIDRs)"
echo "Updated ${ICLOUD_OUT_FILE} (${icloud_count} CIDRs)"
echo "Manual allow file: ${MANUAL_OUT_FILE}"
