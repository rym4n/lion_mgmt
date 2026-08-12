#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VERSION_PREFIX_FILE="${SCRIPT_DIR}/../VERSION_PREFIX"
VERSION_TIMEZONE=Asia/Shanghai

if [[ ! -f ${VERSION_PREFIX_FILE} ]]; then
  echo "missing version prefix file: ${VERSION_PREFIX_FILE}" >&2
  exit 1
fi

VERSION_PREFIX=$(tr -d '[:space:]' < "${VERSION_PREFIX_FILE}")
if [[ ! ${VERSION_PREFIX} =~ ^[0-9]+\.[0-9]+$ ]]; then
  echo "invalid version prefix: ${VERSION_PREFIX}" >&2
  exit 1
fi

generate_tag() {
  local epoch=${1:-}

  if [[ -z ${epoch} ]]; then
    TZ="${VERSION_TIMEZONE}" date "+v${VERSION_PREFIX}.%Y%m%d%H%M"
    return
  fi

  if [[ ! ${epoch} =~ ^[0-9]+$ ]]; then
    echo "invalid generation epoch: ${epoch}" >&2
    return 1
  fi

  TZ="${VERSION_TIMEZONE}" date -d "@${epoch}" "+v${VERSION_PREFIX}.%Y%m%d%H%M"
}

validate_tag() {
  local tag=${1:-}
  local tag_prefix="v${VERSION_PREFIX}."
  local timestamp
  local normalized

  if [[ ${tag} != "${tag_prefix}"* ]]; then
    echo "invalid release tag '${tag}'; expected v${VERSION_PREFIX}.YYYYMMDDHHmm" >&2
    return 1
  fi

  timestamp=${tag#"${tag_prefix}"}
  if [[ ! ${timestamp} =~ ^[0-9]{12}$ ]]; then
    echo "invalid release tag '${tag}'; expected v${VERSION_PREFIX}.YYYYMMDDHHmm" >&2
    return 1
  fi

  normalized=$(
    TZ="${VERSION_TIMEZONE}" date -d \
      "${timestamp:0:4}-${timestamp:4:2}-${timestamp:6:2} ${timestamp:8:2}:${timestamp:10:2}:00" \
      +%Y%m%d%H%M 2>/dev/null
  ) || true

  if [[ ${normalized} != "${timestamp}" ]]; then
    echo "invalid release timestamp '${timestamp}' in timezone ${VERSION_TIMEZONE}" >&2
    return 1
  fi
}

case ${1:-generate} in
  generate)
    generate_tag "${2:-}"
    ;;
  validate)
    validate_tag "${2:-}"
    ;;
  *)
    echo "usage: $0 [generate [epoch] | validate <tag>]" >&2
    exit 2
    ;;
esac
