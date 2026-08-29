# The outer runner must supply a registry reference pinned by digest, for
# example debian:bookworm-slim@sha256:<64 lowercase hex digits>.  An unpinned
# tag is a hard build failure.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG BASE_IMAGE
ARG EXECUTION_VARIANT

USER 0:0
WORKDIR /
ENV LD_PRELOAD="" \
    LD_AUDIT="" \
    LD_LIBRARY_PATH="" \
    PYTHONPATH="" \
    PYTHONHOME="" \
    BASH_ENV="" \
    ENV=""

SHELL ["/bin/sh", "-ec"]

RUN digest="${BASE_IMAGE##*@sha256:}"; \
    [ "$digest" != "$BASE_IMAGE" ]; \
    [ "${#digest}" -eq 64 ]; \
    case "$digest" in *[!0-9a-f]*) exit 64;; esac; \
    case "$EXECUTION_VARIANT" in real|sandbox|stub) ;; *) exit 64;; esac

# The gate base is a separately reviewed and published image.  It must already
# contain these tools; the evidence build is networkless and never runs a
# package manager, so the BASE_IMAGE digest binds the entire userspace.
RUN set -eu; \
    for path in \
        /usr/lib/systemd/systemd \
        /usr/bin/systemctl \
        /usr/bin/systemd-analyze \
        /usr/bin/systemd-tmpfiles \
        /usr/bin/python3 \
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
    [ -r /etc/os-release ] || exit 69

# Fixed, non-root and pairwise-distinct service identities.  They have no
# home, no login shell, no supplementary groups and locked passwords.
RUN groupadd --gid 2001 hepta-gateway; \
    groupadd --gid 2002 hepta-exec; \
    groupadd --gid 2003 hepta-ib-exec; \
    groupadd --gid 2004 hepta-agent; \
    useradd --uid 2001 --gid 2001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-gateway; \
    useradd --uid 2002 --gid 2002 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-exec; \
    useradd --uid 2003 --gid 2003 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-ib-exec; \
    useradd --uid 2004 --gid 2004 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-agent; \
    passwd --lock hepta-gateway >/dev/null; \
    passwd --lock hepta-exec >/dev/null; \
    passwd --lock hepta-ib-exec >/dev/null; \
    passwd --lock hepta-agent >/dev/null

# The outer runner descriptor-copies an exact runtime allowlist. The formal
# IBAPI ELF is represented only by its outer hash and never enters this build
# context. Nothing is bind-mounted from the host at run time.
COPY --chown=0:0 install-root/usr/ /usr/
COPY --chown=0:0 install-root/etc/ /etc/
COPY --chown=0:0 scripts/check_hepta_execution_provisioned_host.py \
    /usr/local/libexec/check_hepta_execution_provisioned_host.py
COPY --chown=0:0 artifacts/hepta_execution_systemd_client_probe \
    /usr/local/libexec/hepta_execution_systemd_client_probe
COPY --chown=0:0 artifacts/hepta_execution_systemd_sandbox_probe \
    /usr/local/libexec/hepta_execution_systemd_sandbox_probe
COPY --chown=0:0 artifacts/hepta-ib-executiond-disabled \
    /usr/local/libexec/hepta-ib-executiond-disabled
COPY --chown=0:0 artifacts/formal-ibapi.sha256 \
    /usr/local/share/hepta-rootful-systemd-gate/formal-ibapi.sha256
COPY --chown=0:0 tests/rootful_systemd/hepta-systemd-entrypoint \
    /usr/local/libexec/hepta-systemd-entrypoint
COPY --chown=0:0 tests/rootful_systemd/hepta_execution_rootful_inner_gate.py \
    /usr/local/libexec/hepta_execution_rootful_inner_gate.py
COPY --chown=0:0 tests/rootful_systemd/hepta_agent_os_identity_permissions.py \
    /usr/local/libexec/hepta_agent_os_identity_permissions.py
COPY --chown=0:0 tests/rootful_systemd/hepta-rootful-systemd-gate.target \
    /usr/lib/systemd/system/hepta-rootful-systemd-gate.target

RUN set -eu; \
    for path in \
        /usr/libexec/hepta-executiond \
        /usr/libexec/hepta-ib-executiond \
        /usr/local/libexec/hepta_execution_systemd_client_probe \
        /usr/local/libexec/hepta_execution_systemd_sandbox_probe \
        /usr/local/libexec/hepta-ib-executiond-disabled; do \
        [ -f "$path" ] && [ ! -L "$path" ]; \
        magic="$(dd if="$path" bs=4 count=1 2>/dev/null)"; \
        [ "$magic" = "$(printf '\177ELF')" ]; \
        readelf --file-header --program-headers --dynamic "$path" \
            >/dev/null 2>&1; \
    done; \
    formal_ib_hash="$(cat \
        /usr/local/share/hepta-rootful-systemd-gate/formal-ibapi.sha256)"; \
    [ "${#formal_ib_hash}" -eq 64 ]; \
    case "$formal_ib_hash" in *[!0-9a-f]*) exit 64;; esac; \
    case "$EXECUTION_VARIANT" in \
        real) install -o 0 -g 0 -m 0755 \
            /usr/local/libexec/hepta-ib-executiond-disabled \
            /usr/libexec/hepta-ib-executiond ;; \
        sandbox) install -o 0 -g 0 -m 0755 \
            /usr/local/libexec/hepta_execution_systemd_sandbox_probe \
            /usr/libexec/hepta-ib-executiond ;; \
        stub) install -o 0 -g 0 -m 0755 \
            /usr/local/libexec/hepta-ib-executiond-disabled \
            /usr/libexec/hepta-ib-executiond ;; \
    esac; \
    printf '%s\n' "$EXECUTION_VARIANT" \
        >/usr/local/share/hepta-rootful-systemd-gate/variant; \
    printf '%s\n' "${BASE_IMAGE##*@sha256:}" \
        >/usr/local/share/hepta-rootful-systemd-gate/base-image-digest; \
    chmod 0444 \
        /usr/local/share/hepta-rootful-systemd-gate/variant \
        /usr/local/share/hepta-rootful-systemd-gate/base-image-digest \
        /usr/local/share/hepta-rootful-systemd-gate/formal-ibapi.sha256; \
    chmod 0755 \
        /usr/local/libexec/check_hepta_execution_provisioned_host.py \
        /usr/local/libexec/hepta-systemd-entrypoint \
        /usr/local/libexec/hepta_execution_rootful_inner_gate.py \
        /usr/local/libexec/hepta_agent_os_identity_permissions.py; \
    /usr/bin/python3 \
        /usr/local/libexec/hepta_agent_os_identity_permissions.py \
        --require-root --root-probe-only

RUN rm -rf \
        /etc/systemd/system/*.wants/* \
        /etc/systemd/system/*.requires/* \
        /var/lib/systemd/random-seed \
        /var/log/*; \
    ln -sfn /usr/lib/systemd/system/hepta-rootful-systemd-gate.target \
        /etc/systemd/system/default.target

ENV HEPTA_ROOTFUL_GATE_MODE=${EXECUTION_VARIANT}

LABEL io.hepta.purpose="rootful-systemd-gate" \
      io.hepta.network="none" \
      io.hepta.real-ibapi-execution="forbidden"

STOPSIGNAL SIGRTMIN+3
HEALTHCHECK NONE
USER 0:0
WORKDIR /
ENTRYPOINT ["/usr/local/libexec/hepta-systemd-entrypoint"]
CMD []
