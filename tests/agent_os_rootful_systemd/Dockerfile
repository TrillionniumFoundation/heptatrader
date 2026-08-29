FROM scratch

ADD base-rootfs.tar /
ARG BASE_IMAGE

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
    case "$digest" in *[!0-9a-f]*) exit 64;; esac

# The build is deliberately networkless.  The pinned base must already contain
# every userspace dependency needed by systemd PID 1 and the local probes.
RUN set -eu; \
    for path in \
        /usr/lib/systemd/systemd \
        /usr/bin/systemctl \
        /usr/bin/systemd-tmpfiles \
        /usr/bin/python3 \
        /usr/bin/getent \
        /usr/bin/id \
        /usr/bin/install \
        /usr/bin/mount \
        /usr/bin/passwd \
        /usr/bin/readelf \
        /usr/bin/findmnt \
        /usr/bin/sleep \
        /usr/sbin/ip \
        /usr/sbin/nft \
        /usr/sbin/groupadd \
        /usr/sbin/useradd; do \
        [ -x "$path" ] || exit 69; \
    done; \
    [ -r /etc/os-release ] || exit 69

# Pairwise-distinct, non-login compatibility identities plus two declarative
# trust-domain identity triples. UID 2003 remains reserved and is verified by
# the gate, but no IB binary or IB/PAPER unit is installed. hepta-gateway is a
# socket-connect group for domain Gateways; its same-name account exists only
# for the explicitly exercised single-domain compatibility units.
RUN groupadd --gid 2001 hepta-gateway; \
    groupadd --gid 2002 hepta-exec; \
    groupadd --gid 2003 hepta-ib-exec; \
    groupadd --gid 2004 hepta-agent; \
    groupadd --gid 2101 hepta-gw-codex-a; \
    groupadd --gid 2104 hepta-agent-codex-a; \
    groupadd --gid 2111 hepta-exec-codex-a; \
    groupadd --gid 2102 hepta-gw-openclaw-b; \
    groupadd --gid 2105 hepta-agent-openclaw-b; \
    groupadd --gid 2112 hepta-exec-openclaw-b; \
    groupadd --gid 2121 hepta-shadow-reader-codex-a; \
    groupadd --gid 2122 hepta-shadow-reader-openclaw-b; \
    useradd --uid 2001 --gid 2001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-gateway; \
    useradd --uid 2002 --gid 2002 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-exec; \
    useradd --uid 2003 --gid 2003 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-ib-exec; \
    useradd --uid 2004 --gid 2004 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-agent; \
    useradd --uid 2101 --gid 2101 --groups hepta-gateway --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-gw-codex-a; \
    useradd --uid 2104 --gid 2104 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-agent-codex-a; \
    useradd --uid 2111 --gid 2111 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-exec-codex-a; \
    useradd --uid 2102 --gid 2102 --groups hepta-gateway --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-gw-openclaw-b; \
    useradd --uid 2105 --gid 2105 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-agent-openclaw-b; \
    useradd --uid 2112 --gid 2112 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-exec-openclaw-b; \
    useradd --uid 2121 --gid 2121 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-shadow-reader-codex-a; \
    useradd --uid 2122 --gid 2122 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-shadow-reader-openclaw-b; \
    passwd --lock hepta-gateway >/dev/null; \
    passwd --lock hepta-exec >/dev/null; \
    passwd --lock hepta-ib-exec >/dev/null; \
    passwd --lock hepta-agent >/dev/null; \
    passwd --lock hepta-gw-codex-a >/dev/null; \
    passwd --lock hepta-agent-codex-a >/dev/null; \
    passwd --lock hepta-exec-codex-a >/dev/null; \
    passwd --lock hepta-gw-openclaw-b >/dev/null; \
    passwd --lock hepta-agent-openclaw-b >/dev/null; \
    passwd --lock hepta-exec-openclaw-b >/dev/null; \
    passwd --lock hepta-shadow-reader-codex-a >/dev/null; \
    passwd --lock hepta-shadow-reader-openclaw-b >/dev/null

COPY --chown=0:0 install-root/usr/ /usr/
COPY --chown=0:0 install-root/etc/ /etc/
COPY --chown=0:0 \
    tests/agent_os_rootful_systemd/hepta-agent-os-systemd-entrypoint \
    /usr/local/libexec/hepta-agent-os-systemd-entrypoint
COPY --chown=0:0 \
    tests/agent_os_rootful_systemd/hepta_agent_os_rootful_inner_gate.py \
    /usr/local/libexec/hepta_agent_os_rootful_inner_gate.py
COPY --chown=0:0 \
    tests/agent_os_rootful_systemd/hepta_broker_network_rootful_probe.py \
    /usr/local/libexec/hepta_broker_network_rootful_probe.py
COPY --chown=0:0 \
    tests/agent_os_rootful_systemd/hepta-agent-os-rootful-e2e.target \
    /usr/lib/systemd/system/hepta-agent-os-rootful-e2e.target

RUN set -eu; \
    for path in \
        /usr/libexec/hepta-executiond \
        /usr/libexec/hepta-tool-gatewayd \
        /usr/bin/hepta-sessionctl \
        /usr/bin/heptactl; do \
        [ -f "$path" ] && [ ! -L "$path" ]; \
        magic="$(dd if="$path" bs=4 count=1 2>/dev/null)"; \
        [ "$magic" = "$(printf '\177ELF')" ]; \
        readelf --file-header --program-headers --dynamic "$path" \
            >/dev/null 2>&1; \
    done; \
    [ ! -e /usr/libexec/hepta-ib-executiond ]; \
    [ ! -e /usr/lib/systemd/system/hepta-execution-ib-paper.service ]; \
    [ ! -e /usr/lib/systemd/system/hepta-execution-ib-paper.socket ]; \
    [ ! -e /usr/lib/systemd/system/hepta-execution-events-ib-paper.socket ]; \
    chmod 0755 \
        /usr/local/libexec/hepta-agent-os-systemd-entrypoint \
        /usr/local/libexec/hepta_agent_os_rootful_inner_gate.py \
        /usr/local/libexec/hepta_broker_network_rootful_probe.py; \
    chmod 0644 \
        /usr/lib/systemd/system/hepta-agent-os-rootful-e2e.target; \
    /usr/libexec/check-hepta-agent-os-provisioned-host --installation-only; \
    printf '%s\n' HEPTA_AGENT_OS_INSTALLATION_PREFLIGHT_V1 \
        >/usr/local/share/hepta-agent-os-e2e/installation-preflight; \
    chmod 0444 \
        /usr/local/share/hepta-agent-os-e2e/installation-preflight

# The image contains only the reviewed fail-closed build-time provisioning
# needed by the installation preflight.  At runtime a dedicated
# /etc/heptatrader tmpfs hides it; the entrypoint reconstructs the reviewed
# non-secret profiles, the deny-all identity contract, and an ephemeral random
# lease-store key.
RUN rm -rf \
        /etc/systemd/system/*.wants/* \
        /etc/systemd/system/*.requires/* \
        /var/lib/systemd/random-seed \
        /var/log/*; \
    ln -sfn /usr/lib/systemd/system/hepta-agent-os-rootful-e2e.target \
        /etc/systemd/system/default.target

LABEL io.hepta.purpose="agent-os-rootful-systemd-e2e-gate" \
      io.hepta.network="none" \
      io.hepta.authority="watch-only" \
      io.hepta.ib-adapter="absent"

STOPSIGNAL SIGRTMIN+3
HEALTHCHECK NONE
USER 0:0
WORKDIR /
ENTRYPOINT ["/usr/local/libexec/hepta-agent-os-systemd-entrypoint"]
CMD []
