#!/bin/sh

set -eu
set -f

RELEASES=${EVDB_RELEASES:-https://github.com/evannotfound/evdb/releases}
DESTDIR=${DESTDIR:-}
COMMAND="${DESTDIR}/usr/local/bin/evdb"
CONFIG="${DESTDIR}/etc/evdb/config.yml"
VERSION=${1:-}
MAX_RELEASE_SIZE=${EVDB_MAX_RELEASE_SIZE:-268435456}
STAGE=
TEMP=
LOCK="${COMMAND}.install.lock"
LOCK_CREATED=0
CONFIGURED=0

fail() {
    printf '%s\n' "evdb: $*" >&2
    exit 1
}

cleanup() {
    if [ -n "${TEMP}" ]; then
        rm -f "${TEMP}"
    fi
    if [ -n "${STAGE}" ]; then
        rm -rf "${STAGE}"
    fi
    if [ "${LOCK_CREATED}" -eq 1 ]; then
        rmdir "${LOCK}"
    fi
}

valid_version() {
    VALUE=$1
    printf '%s\n' "${VALUE}" | grep -Eq \
        '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$' || \
        return 1
    case "${VALUE}" in
        *-*)
            PRERELEASE=${VALUE#*-}
            PRERELEASE=${PRERELEASE%%+*}
            OLD_IFS=${IFS}
            IFS=.
            set -- ${PRERELEASE}
            IFS=${OLD_IFS}
            for IDENTIFIER do
                if printf '%s\n' "${IDENTIFIER}" | grep -Eq '^[0-9]+$'; then
                    case "${IDENTIFIER}" in
                        0|[1-9][0-9]*) ;;
                        *) return 1 ;;
                    esac
                fi
            done
            ;;
    esac
}

download() {
    URL=$1
    DESTINATION=$2
    LIMIT=$3
    BLOCKS=$(((LIMIT + 511) / 512))
    (
        ulimit -f "${BLOCKS}"
        curl -fsSL --max-filesize "${LIMIT}" "${URL}" -o "${DESTINATION}"
    )
    [ "$(wc -c < "${DESTINATION}")" -le "${LIMIT}" ] || \
        fail "release download exceeds the size limit"
}

trap cleanup EXIT
trap 'exit 1' HUP INT TERM

if [ -z "${DESTDIR}" ] && [ "$(id -u)" -ne 0 ]; then
    fail "installation requires root; rerun with sudo"
fi

SYSTEM=${EVDB_UNAME_S:-$(uname -s)}
MACHINE=${EVDB_UNAME_M:-$(uname -m)}
case "${SYSTEM}" in
    Linux|linux) ;;
    *) fail "unsupported operating system: ${SYSTEM}" ;;
esac
case "${MACHINE}" in
    aarch64|arm64) ARCH=arm64 ;;
    amd64|x86_64) ARCH=amd64 ;;
    *) fail "unsupported architecture: ${MACHINE}" ;;
esac

case "${MAX_RELEASE_SIZE}" in
    ''|*[!0-9]*) fail "release size limit must be a positive integer" ;;
esac
[ "${MAX_RELEASE_SIZE}" -gt 0 ] || fail "release size limit must be a positive integer"
if [ -n "${VERSION}" ] && ! valid_version "${VERSION}"; then
    fail "version must be an exact semantic version, for example 1.2.3"
fi

for command in chmod curl dirname grep id install mkdir mktemp mv rm rmdir sha256sum uname wc; do
    command -v "${command}" >/dev/null 2>&1 || fail "required command is missing: ${command}"
done

install -d -m 0755 "$(dirname "${COMMAND}")"
if [ -L "${COMMAND}" ] || { [ -e "${COMMAND}" ] && [ ! -f "${COMMAND}" ]; }; then
    fail "installed command path is unsafe: ${COMMAND}"
fi
if [ -e "${CONFIG}" ] || [ -L "${CONFIG}" ]; then
    [ -f "${CONFIG}" ] && [ ! -L "${CONFIG}" ] || \
        fail "managed configuration is invalid: ${CONFIG}"
    CONFIGURED=1
fi
if ! mkdir "${LOCK}" 2>/dev/null; then
    fail "another evdb installation is in progress"
fi
LOCK_CREATED=1

ASSET="evdb_linux_${ARCH}"
if [ -n "${VERSION}" ]; then
    BASE="${RELEASES}/download/v${VERSION}"
else
    BASE="${RELEASES}/latest/download"
fi

STAGE=$(mktemp -d)
CANDIDATE="${STAGE}/${ASSET}"
CHECKSUM="${CANDIDATE}.sha256"
download "${BASE}/${ASSET}" "${CANDIDATE}" "${MAX_RELEASE_SIZE}"
download "${BASE}/${ASSET}.sha256" "${CHECKSUM}" 1024

COUNT=0
EXPECTED=
CHECKSUM_NAME=
while read -r HASH NAME; do
    COUNT=$((COUNT + 1))
    EXPECTED=${HASH}
    CHECKSUM_NAME=${NAME}
done < "${CHECKSUM}"
[ "${COUNT}" -eq 1 ] || fail "release checksum file is malformed"
[ "${CHECKSUM_NAME}" = "${ASSET}" ] || fail "release checksum file names another asset"
printf '%s\n' "${EXPECTED}" | grep -Eq '^[0-9a-fA-F]{64}$' || \
    fail "release checksum file is malformed"
ACTUAL=$(sha256sum "${CANDIDATE}")
ACTUAL=${ACTUAL%% *}
[ "${ACTUAL}" = "${EXPECTED}" ] || fail "release checksum does not match downloaded executable"

chmod 0755 "${CANDIDATE}"
REPORTED=$("${CANDIDATE}" --version) || fail "release executable did not report a version"
case "${REPORTED}" in
    "evdb "*) RESOLVED=${REPORTED#evdb } ;;
    *) fail "release executable returned an invalid version" ;;
esac
valid_version "${RESOLVED}" || fail "release executable returned an invalid version"
[ -z "${VERSION}" ] || [ "${RESOLVED}" = "${VERSION}" ] || \
    fail "release executable version does not match ${VERSION}"

UPGRADE=0
if [ -e "${COMMAND}" ]; then
    UPGRADE=1
fi
TEMP="${COMMAND}.new.$$"
[ ! -e "${TEMP}" ] && [ ! -L "${TEMP}" ] || fail "temporary command path already exists"
install -m 0755 "${CANDIDATE}" "${TEMP}"
mv -f "${TEMP}" "${COMMAND}"
TEMP=

if [ "${UPGRADE}" -eq 1 ]; then
    printf '%s\n' "Upgraded evdb to ${RESOLVED}."
else
    printf '%s\n' "Installed evdb ${RESOLVED}."
fi
if [ "${CONFIGURED}" -eq 1 ]; then
    "${COMMAND}" init --yes
else
    printf '%s\n' "Next: sudo evdb init"
fi
