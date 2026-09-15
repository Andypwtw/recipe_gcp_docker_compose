#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/recipe-multinic.env}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

sysctl -w net.ipv4.ip_forward=1 >/dev/null
sysctl -w net.ipv4.conf.all.forwarding=1 >/dev/null

# Loose reverse-path filtering is safer for a multi-homed VM.
sysctl -w net.ipv4.conf.all.rp_filter=2 >/dev/null
sysctl -w "net.ipv4.conf.${IFACE_1}.rp_filter=2" >/dev/null
sysctl -w "net.ipv4.conf.${IFACE_2}.rp_filter=2" >/dev/null
sysctl -w "net.ipv4.conf.${IFACE_3}.rp_filter=2" >/dev/null
sysctl -w "net.ipv4.conf.${IFACE_4}.rp_filter=2" >/dev/null

configure_route() {
  local index="$1"
  local iface="$2"
  local source_ip="$3"
  local subnet="$4"
  local gateway="$5"
  local table="$((100 + index))"

  ip route replace "${subnet}" dev "${iface}" src "${source_ip}" table "${table}"
  ip route replace default via "${gateway}" dev "${iface}" table "${table}"

  while ip rule del from "${source_ip}/32" table "${table}" 2>/dev/null; do
    :
  done

  ip rule add from "${source_ip}/32" table "${table}"
}

configure_route 1 "${IFACE_1}" "${PRIVATE_IP_1}" "${SUBNET_1}" "${GATEWAY_1}"
configure_route 2 "${IFACE_2}" "${PRIVATE_IP_2}" "${SUBNET_2}" "${GATEWAY_2}"
configure_route 3 "${IFACE_3}" "${PRIVATE_IP_3}" "${SUBNET_3}" "${GATEWAY_3}"
configure_route 4 "${IFACE_4}" "${PRIVATE_IP_4}" "${SUBNET_4}" "${GATEWAY_4}"

ip route flush cache

echo "Multi-NIC policy routing configured."
ip rule show
