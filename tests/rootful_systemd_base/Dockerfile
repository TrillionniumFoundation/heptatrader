# syntax=docker/dockerfile:1.7

# Build this image in a separately reviewed network-enabled step, publish it to
# a reviewed registry, and use only its immutable RepoDigest for gate builds.
# All actual rootful-gate Dockerfiles build with network=none.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS rootfs

ARG BASE_IMAGE
ARG APT_MIRROR=http://archive.ubuntu.com/ubuntu
ARG APT_SECURITY_MIRROR=http://security.ubuntu.com/ubuntu
ARG DEBIAN_FRONTEND=noninteractive

USER 0:0
WORKDIR /
ENV LANG=C \
    LC_ALL=C \
    TZ=UTC
SHELL ["/bin/sh", "-ec"]

RUN digest="${BASE_IMAGE##*@sha256:}"; \
    [ "$digest" != "$BASE_IMAGE" ]; \
    [ "${#digest}" -eq 64 ]; \
    case "$digest" in *[!0-9a-f]*) exit 64;; esac; \
    . /etc/os-release; \
    [ "$ID" = ubuntu ]; \
    [ "$VERSION_ID" = 24.04 ]; \
    [ "${VERSION_CODENAME:-}" = noble ]; \
    [ "$(dpkg --print-architecture)" = amd64 ]; \
    case "$APT_MIRROR" in http://archive.ubuntu.com/ubuntu) ;; *) exit 64;; esac; \
    case "$APT_SECURITY_MIRROR" in \
        http://security.ubuntu.com/ubuntu) ;; *) exit 64;; esac; \
    printf '%s\n' \
        "deb $APT_MIRROR noble main universe" \
        "deb $APT_MIRROR noble-updates main universe" \
        "deb $APT_SECURITY_MIRROR noble-security main universe" \
        >/etc/apt/sources.list; \
    rm -f /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; \
    apt-get -o Acquire::Languages=none update; \
    apt-get install -y --no-install-recommends \
        binutils \
        coreutils \
        iproute2 \
        libc-bin \
        mount \
        nftables \
        passwd \
        python3-minimal \
        python3.12-minimal \
        systemd \
        tzdata \
        util-linux; \
    for path in \
        /usr/lib/systemd/systemd \
        /usr/bin/systemctl \
        /usr/bin/systemd-analyze \
        /usr/bin/systemd-tmpfiles \
        /usr/bin/journalctl \
        /usr/bin/python3 \
        /usr/bin/python3.12 \
        /usr/bin/setpriv \
        /usr/bin/nsenter \
        /usr/bin/findmnt \
        /usr/bin/getent \
        /usr/bin/readelf \
        /usr/bin/passwd \
        /usr/sbin/ip \
        /usr/sbin/nft \
        /usr/sbin/groupadd \
        /usr/sbin/useradd; do \
        [ -x "$path" ] || exit 69; \
    done; \
    /usr/bin/python3 -c \
        'import sys; assert sys.version_info[:2] == (3, 12)'; \
    /usr/bin/python3.12 -c \
        'import sys; assert sys.version_info[:2] == (3, 12)'; \
    install -d -m 0755 /usr/local/share/hepta-rootful-systemd-base; \
    printf '%s\n' "$BASE_IMAGE" \
        >/usr/local/share/hepta-rootful-systemd-base/transport-base.ref; \
    sha256sum /etc/os-release | cut -d' ' -f1 \
        >/usr/local/share/hepta-rootful-systemd-base/os-release.sha256; \
    dpkg-query -W -f='${binary:Package}=${Version}\n' \
        | LC_ALL=C sort \
        >/usr/local/share/hepta-rootful-systemd-base/packages.tsv; \
    sha256sum /usr/local/share/hepta-rootful-systemd-base/packages.tsv \
        | cut -d' ' -f1 \
        >/usr/local/share/hepta-rootful-systemd-base/packages.sha256; \
    chmod 0444 /usr/local/share/hepta-rootful-systemd-base/packages.tsv \
        /usr/local/share/hepta-rootful-systemd-base/packages.sha256 \
        /usr/local/share/hepta-rootful-systemd-base/transport-base.ref \
        /usr/local/share/hepta-rootful-systemd-base/os-release.sha256; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/* /var/log/* \
        /tmp/* /var/tmp/* /var/lib/systemd/random-seed; \
    rm -f /etc/machine-id /var/lib/dbus/machine-id

# Re-root through scratch so the reviewed gate image does not inherit labels,
# volumes, entrypoints, health checks or on-build instructions from the distro
# transport image.  The runner requires the two labels below to be the complete
# label map.
FROM scratch
COPY --from=rootfs / /

LABEL io.hepta.rootful-systemd-base.offline-ready="true" \
      io.hepta.rootful-systemd-base.version="1"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC
STOPSIGNAL SIGRTMIN+3
HEALTHCHECK NONE
USER 0:0
WORKDIR /
CMD ["/usr/lib/systemd/systemd"]
