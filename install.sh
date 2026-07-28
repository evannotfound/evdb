#!/bin/sh

set -eu
set -f

RELEASES=${EVDB_RELEASES:-https://github.com/evannotfound/evdb/releases}
DESTDIR=${DESTDIR:-}
ROOT="${DESTDIR}/opt/evdb"
VERSIONS="${ROOT}/versions"
STABLE="${DESTDIR}/usr/local/bin/evdb"
CONFIG="${DESTDIR}/etc/evdb/config.yml"
VERSION=${1:-}
MAX_RELEASE_SIZE=${EVDB_MAX_RELEASE_SIZE:-268435456}
STAGE=
TARGET=
CURRENT_TEMP=
PREVIOUS_TEMP=
STABLE_TEMP=
UPGRADE=0
CONFIGURED=0
OLD_CURRENT=
OLD_PREVIOUS=
PREVIOUS_EXISTS=0
TARGET_CREATED=0
CURRENT_CREATED=0
PREVIOUS_CREATED=0
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
        if [ "${TARGET_CREATED}" -eq 1 ]; then
            rm -rf "${TARGET}"
        fi
        if [ "${PREVIOUS_CREATED}" -eq 1 ]; then
            rm -f "${ROOT}/previous"
            if [ "${PREVIOUS_EXISTS}" -eq 1 ]; then
                ln -s "${OLD_PREVIOUS}" "${ROOT}/previous"
            fi
        fi
        if [ "${STABLE_CREATED}" -eq 1 ]; then
            rm -f "${STABLE}"
        fi
        if [ "${CURRENT_CREATED}" -eq 1 ]; then
            rm -f "${ROOT}/current"
            if [ "${UPGRADE}" -eq 1 ]; then
                ln -s "${OLD_CURRENT}" "${ROOT}/current"
            fi
        fi
    fi
    if [ -n "${CURRENT_TEMP}" ]; then
        rm -f "${CURRENT_TEMP}"
    fi
    if [ -n "${PREVIOUS_TEMP}" ]; then
        rm -f "${PREVIOUS_TEMP}"
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

version_link() {
    PATHNAME=$1
    LABEL=$2
    [ -L "${PATHNAME}" ] || fail "managed ${LABEL} link is invalid: ${PATHNAME}"
    TARGET_LINK=$(readlink "${PATHNAME}") || fail "managed ${LABEL} link is unreadable: ${PATHNAME}"
    case "${TARGET_LINK}" in
        versions/*)
            SELECTED=${TARGET_LINK#versions/}
            case "${SELECTED}" in
                */*) fail "managed ${LABEL} link is invalid: ${PATHNAME}" ;;
            esac
            ;;
        "${VERSIONS}"/*)
            SELECTED=${TARGET_LINK#"${VERSIONS}/"}
            case "${SELECTED}" in
                */*) fail "managed ${LABEL} link is invalid: ${PATHNAME}" ;;
            esac
            ;;
        *) fail "managed ${LABEL} link points outside managed versions: ${PATHNAME}" ;;
    esac
    valid_version "${SELECTED}" || fail "managed ${LABEL} link does not select an exact version: ${PATHNAME}"
    VERSION_DIR="${VERSIONS}/${SELECTED}"
    [ -d "${VERSION_DIR}" ] && [ ! -L "${VERSION_DIR}" ] || \
        fail "managed ${LABEL} version directory is invalid: ${VERSION_DIR}"
    [ -x "${VERSION_DIR}/bin/evdb" ] && [ ! -L "${VERSION_DIR}/bin/evdb" ] || \
        fail "managed ${LABEL} version has no executable evdb command: ${VERSION_DIR}"
    REPORTED=$("${VERSION_DIR}/bin/evdb" --version 2>&1) || \
        fail "managed ${LABEL} version executable failed: ${VERSION_DIR}"
    [ "${REPORTED}" = "evdb ${SELECTED}" ] || \
        fail "managed ${LABEL} version executable is mismatched: ${VERSION_DIR}"
    printf '%s\n' "${TARGET_LINK}"
}

prune_versions() {
    KEEP_CURRENT=$1
    KEEP_PREVIOUS=$2
    set +f
    for ENTRY in "${VERSIONS}/"*; do
        if [ ! -e "${ENTRY}" ] && [ ! -L "${ENTRY}" ]; then
            continue
        fi
        NAME=${ENTRY##*/}
        if [ "${NAME}" = "${KEEP_CURRENT}" ] || [ "${NAME}" = "${KEEP_PREVIOUS}" ]; then
            continue
        fi
        valid_version "${NAME}" || fail "managed versions root contains an unexpected entry: ${ENTRY}"
        if [ -L "${ENTRY}" ] || [ ! -d "${ENTRY}" ]; then
            fail "managed version entry is not a safe directory: ${ENTRY}"
        fi
        rm -rf "${ENTRY}"
    done
    set -f
}

validate_versions() {
    set +f
    for ENTRY in "${VERSIONS}/"*; do
        if [ ! -e "${ENTRY}" ] && [ ! -L "${ENTRY}" ]; then
            continue
        fi
        NAME=${ENTRY##*/}
        valid_version "${NAME}" || fail "managed versions root contains an unexpected entry: ${ENTRY}"
        [ -d "${ENTRY}" ] && [ ! -L "${ENTRY}" ] || \
            fail "managed version entry is not a safe directory: ${ENTRY}"
    done
    set -f
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

for command in chmod curl dirname grep install ln mkdir mktemp mv readlink rm rmdir sha256sum sort tar uniq wc; do
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
    if [ -e "${CONFIG}" ] || [ -L "${CONFIG}" ]; then
        [ -f "${CONFIG}" ] && [ ! -L "${CONFIG}" ] || \
            fail "managed configuration is invalid: ${CONFIG}"
        CONFIGURED=1
    fi
    UPGRADE=1
    OLD_CURRENT=$(version_link "${ROOT}/current" "current")
    if [ -e "${ROOT}/previous" ] || [ -L "${ROOT}/previous" ]; then
        OLD_PREVIOUS=$(version_link "${ROOT}/previous" "previous")
        PREVIOUS_EXISTS=1
    fi
    [ -L "${STABLE}" ] || fail "stable command path is invalid: ${STABLE}"
    [ "$(readlink "${STABLE}")" = "${ROOT}/current/bin/evdb" ] || \
        fail "stable command path is invalid: ${STABLE}"
elif [ -e "${STABLE}" ] || [ -L "${STABLE}" ]; then
    fail "stable command path already exists: ${STABLE}"
fi

ASSET="evdb_linux_${ARCH}.tar.gz"
if [ -n "${VERSION}" ]; then
    BASE="${RELEASES}/download/v${VERSION}"
else
    BASE="${RELEASES}/latest/download"
fi

install -d -m 0755 "${VERSIONS}"
validate_versions
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
        units/evdb-backup.service|units/evdb-backup.timer) ;;
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
for UNIT in evdb-backup.service evdb-backup.timer; do
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
if [ -e "${TARGET}" ] || [ -L "${TARGET}" ]; then
    fail "version is already installed: ${RESOLVED}"
fi
install -d -m 0755 "$(dirname "${STABLE}")"
CURRENT_TEMP="${ROOT}/.current.new.$$"
PREVIOUS_TEMP="${ROOT}/.previous.new.$$"
STABLE_TEMP="${STABLE}.new.$$"
[ ! -e "${CURRENT_TEMP}" ] && [ ! -L "${CURRENT_TEMP}" ] || \
    fail "temporary current link already exists"
[ ! -e "${PREVIOUS_TEMP}" ] && [ ! -L "${PREVIOUS_TEMP}" ] || \
    fail "temporary previous link already exists"
[ ! -e "${STABLE_TEMP}" ] && [ ! -L "${STABLE_TEMP}" ] || \
    fail "temporary stable link already exists"
ln -s "versions/${RESOLVED}" "${CURRENT_TEMP}"
if [ "${UPGRADE}" -eq 1 ]; then
    ln -s "${OLD_CURRENT}" "${PREVIOUS_TEMP}"
    PREVIOUS_CREATED=1
    rm -f "${ROOT}/previous"
    mv "${PREVIOUS_TEMP}" "${ROOT}/previous"
    PREVIOUS_TEMP=
else
    ln -s "${ROOT}/current/bin/evdb" "${STABLE_TEMP}"
fi
mv "${RELEASE}" "${TARGET}"
TARGET_CREATED=1
CURRENT_CREATED=1
rm -f "${ROOT}/current"
mv "${CURRENT_TEMP}" "${ROOT}/current"
CURRENT_TEMP=
if [ "${UPGRADE}" -eq 0 ]; then
    STABLE_CREATED=1
    mv "${STABLE_TEMP}" "${STABLE}"
    STABLE_TEMP=
fi
version_link "${ROOT}/current" "current" >/dev/null
CURRENT_VERSION=${SELECTED}
PREVIOUS_VERSION=
if [ -e "${ROOT}/previous" ] || [ -L "${ROOT}/previous" ]; then
    version_link "${ROOT}/previous" "previous" >/dev/null
    PREVIOUS_VERSION=${SELECTED}
fi
prune_versions "${CURRENT_VERSION}" "${PREVIOUS_VERSION}"
COMMITTED=1

if [ "${UPGRADE}" -eq 1 ]; then
    printf '%s\n' "Upgraded evdb to ${RESOLVED}."
else
    printf '%s\n' "Installed evdb ${RESOLVED}."
fi
if [ "${CONFIGURED}" -eq 1 ]; then
    "${ROOT}/current/bin/evdb" init --yes
else
    printf '%s\n' "Next: sudo evdb init"
fi
