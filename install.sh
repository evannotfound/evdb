#!/bin/sh

set -eu
set -f

RELEASES=${EVDB_RELEASES:-https://github.com/evannotfound/evdb/releases}
DESTDIR=${DESTDIR:-}
ROOT="${DESTDIR}/opt/evdb"
VERSIONS="${ROOT}/versions"
STABLE="${DESTDIR}/usr/local/bin/evdb"
VERSION=${1:-}
MAX_RELEASE_SIZE=${EVDB_MAX_RELEASE_SIZE:-268435456}
STAGE=
TARGET=
CURRENT_TEMP=
STABLE_TEMP=
TARGET_CREATED=0
CURRENT_CREATED=0
STABLE_CREATED=0
COMMITTED=0
LOCK=
LOCK_CREATED=0

fail() {
    printf '%s\n' "evdb: $*" >&2
    exit 1
}

cleanup() {
    if [ "${COMMITTED}" -eq 0 ]; then
        if [ "${STABLE_CREATED}" -eq 1 ]; then
            rm -f "${STABLE}"
        fi
        if [ "${CURRENT_CREATED}" -eq 1 ]; then
            rm -f "${ROOT}/current"
        fi
        if [ "${TARGET_CREATED}" -eq 1 ]; then
            rm -rf "${TARGET}"
        fi
    fi
    if [ -n "${CURRENT_TEMP}" ]; then
        rm -f "${CURRENT_TEMP}"
    fi
    if [ -n "${STABLE_TEMP}" ]; then
        rm -f "${STABLE_TEMP}"
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

if [ -L "${ROOT}" ] || { [ -e "${ROOT}" ] && [ ! -d "${ROOT}" ]; }; then
    fail "managed tool root is not a safe directory: ${ROOT}"
fi
if [ -L "${VERSIONS}" ] || { [ -e "${VERSIONS}" ] && [ ! -d "${VERSIONS}" ]; }; then
    fail "managed versions root is not a safe directory: ${VERSIONS}"
fi

for command in chmod curl dirname grep install ln mkdir mktemp mv rm rmdir sha256sum sort tar uniq wc; do
    command -v "${command}" >/dev/null 2>&1 || fail "required command is missing: ${command}"
done

if [ -n "${VERSION}" ] && ! valid_version "${VERSION}"; then
    fail "version must be an exact semantic version, for example 1.2.3"
fi

install -d -m 0755 "${ROOT}"
LOCK="${ROOT}/.install.lock"
if ! mkdir "${LOCK}" 2>/dev/null; then
    fail "another evdb installation is in progress"
fi
LOCK_CREATED=1

if [ -e "${ROOT}/current" ] || [ -L "${ROOT}/current" ]; then
    fail "evdb is already installed; use evdb host update VERSION"
fi
if [ -e "${STABLE}" ] || [ -L "${STABLE}" ]; then
    fail "stable command path already exists: ${STABLE}"
fi

ASSET="evdb_linux_${ARCH}.tar.gz"
if [ -n "${VERSION}" ]; then
    BASE="${RELEASES}/download/v${VERSION}"
else
    BASE="${RELEASES}/latest/download"
fi

install -d -m 0755 "${VERSIONS}"
STAGE=$(mktemp -d "${VERSIONS}/.install.XXXXXXXX")
ARCHIVE="${STAGE}/${ASSET}"
CHECKSUM="${ARCHIVE}.sha256"
RELEASE="${STAGE}/release"
LIST="${STAGE}/members"
VERBOSE="${STAGE}/members.verbose"

download "${BASE}/${ASSET}" "${ARCHIVE}" "${MAX_RELEASE_SIZE}"
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
ACTUAL=$(sha256sum "${ARCHIVE}")
ACTUAL=${ACTUAL%% *}
[ "${ACTUAL}" = "${EXPECTED}" ] || fail "release checksum does not match downloaded archive"

tar -tzf "${ARCHIVE}" > "${LIST}"
tar -tvzf "${ARCHIVE}" > "${VERBOSE}"
[ -s "${LIST}" ] || fail "release archive is empty"
[ -z "$(sort "${LIST}" | uniq -d)" ] || fail "release archive contains duplicate members"

while IFS= read -r NAME; do
    case "${NAME}" in
        bin/evdb) ;;
        units/evdb-*.service|units/evdb-*.timer)
            case "${NAME#units/}" in
                */*) fail "release archive contains a nested unit path: ${NAME}" ;;
            esac
            ;;
        *) fail "release archive contains an unexpected member: ${NAME}" ;;
    esac
done < "${LIST}"

TOTAL_SIZE=0
while IFS= read -r DETAILS; do
    TYPE=${DETAILS%"${DETAILS#?}"}
    [ "${TYPE}" = "-" ] || fail "release archive contains a non-regular member"
    set -- ${DETAILS}
    [ "$#" -eq 6 ] || fail "release archive contains an invalid member record"
    MEMBER_SIZE=$3
    case "${MEMBER_SIZE}" in
        ''|*[!0-9]*) fail "release archive contains an invalid member size" ;;
    esac
    TOTAL_SIZE=$((TOTAL_SIZE + MEMBER_SIZE))
    [ "${TOTAL_SIZE}" -le "${MAX_RELEASE_SIZE}" ] || \
        fail "release archive expands beyond the size limit"
done < "${VERBOSE}"

grep -Fx 'bin/evdb' "${LIST}" >/dev/null || fail "release archive has no evdb executable"
for UNIT in \
    evdb-backup@.service evdb-backup@.timer \
    evdb-backup-test.service evdb-backup-test.timer \
    evdb-prune.service evdb-prune.timer \
    evdb-repository-check.service evdb-repository-check.timer \
    evdb-retention.service evdb-retention.timer \
    evdb-status.service evdb-status.timer; do
    grep -Fx "units/${UNIT}" "${LIST}" >/dev/null || \
        fail "release archive is missing canonical unit: ${UNIT}"
done

mkdir -m 0755 "${RELEASE}"
tar -xzf "${ARCHIVE}" -C "${RELEASE}" --no-same-owner --no-same-permissions
chmod 0755 "${RELEASE}/bin/evdb"
while IFS= read -r NAME; do
    case "${NAME}" in
        units/*) chmod 0644 "${RELEASE}/${NAME}" ;;
    esac
done < "${LIST}"

REPORTED=$("${RELEASE}/bin/evdb" --version) || fail "release executable did not report a version"
case "${REPORTED}" in
    "evdb "*) RESOLVED=${REPORTED#evdb } ;;
    *) fail "release executable returned an invalid version" ;;
esac
printf '%s\n' "${RESOLVED}" | grep -Eq \
    '^[0-9A-Za-z.+-]+$' || fail "release executable returned an invalid version"
valid_version "${RESOLVED}" || fail "release executable returned an invalid version"
[ -z "${VERSION}" ] || [ "${RESOLVED}" = "${VERSION}" ] || \
    fail "release executable version does not match ${VERSION}"

TARGET="${VERSIONS}/${RESOLVED}"
[ ! -e "${TARGET}" ] && [ ! -L "${TARGET}" ] || fail "version is already installed: ${RESOLVED}"
install -d -m 0755 "$(dirname "${STABLE}")"
CURRENT_TEMP="${ROOT}/.current.new.$$"
STABLE_TEMP="${STABLE}.new.$$"
[ ! -e "${CURRENT_TEMP}" ] && [ ! -L "${CURRENT_TEMP}" ] || \
    fail "temporary current link already exists"
[ ! -e "${STABLE_TEMP}" ] && [ ! -L "${STABLE_TEMP}" ] || \
    fail "temporary stable link already exists"
ln -s "versions/${RESOLVED}" "${CURRENT_TEMP}"
ln -s "${ROOT}/current/bin/evdb" "${STABLE_TEMP}"
TARGET_CREATED=1
mv "${RELEASE}" "${TARGET}"
CURRENT_CREATED=1
mv "${CURRENT_TEMP}" "${ROOT}/current"
CURRENT_TEMP=
STABLE_CREATED=1
mv "${STABLE_TEMP}" "${STABLE}"
STABLE_TEMP=
COMMITTED=1

printf '%s\n' "Installed evdb ${RESOLVED}."
printf '%s\n' "Next: sudo evdb host setup"
