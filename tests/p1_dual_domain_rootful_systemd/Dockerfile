ARG BASE_IMAGE
FROM ${BASE_IMAGE}

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

# The base is resolved before this networkless build starts.  Keep an in-image
# check so a future runner cannot silently weaken the digest contract.
RUN digest="${BASE_IMAGE##*@sha256:}"; \
    [ "$digest" != "$BASE_IMAGE" ]; \
    [ "${#digest}" -eq 64 ]; \
    case "$digest" in *[!0-9a-f]*) exit 64;; esac; \
    for path in \
        /usr/lib/systemd/systemd \
        /usr/bin/systemctl \
        /usr/bin/python3 \
        /usr/bin/install \
        /usr/bin/mount \
        /usr/bin/findmnt \
        /usr/bin/passwd \
        /usr/sbin/groupadd \
        /usr/sbin/useradd; do \
        [ -x "$path" ] || exit 69; \
    done

# WATCH and inert PAPER identities are pairwise distinct across both trust
# domains.  No compatibility identity, IB adapter or broker user is present.
RUN groupadd --gid 2211 hepta-p1-watch-codex-a; \
    groupadd --gid 2212 hepta-p1-watch-openclaw-b; \
    groupadd --gid 2231 hepta-p1-paper-codex-a; \
    groupadd --gid 2232 hepta-p1-paper-openclaw-b; \
    useradd --uid 2211 --gid 2211 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-p1-watch-codex-a; \
    useradd --uid 2212 --gid 2212 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-p1-watch-openclaw-b; \
    useradd --uid 2231 --gid 2231 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-p1-paper-codex-a; \
    useradd --uid 2232 --gid 2232 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-p1-paper-openclaw-b; \
    passwd --lock hepta-p1-watch-codex-a >/dev/null; \
    passwd --lock hepta-p1-watch-openclaw-b >/dev/null; \
    passwd --lock hepta-p1-paper-codex-a >/dev/null; \
    passwd --lock hepta-p1-paper-openclaw-b >/dev/null

COPY --chown=0:0 install-root/usr/ /usr/
COPY --chown=0:0 provision-root/ /opt/hepta-p1-dual/provision/
COPY --chown=0:0 \
    tests/p1_dual_domain_rootful_systemd/hepta-p1-dual-domain-systemd-entrypoint \
    /usr/local/libexec/hepta-p1-dual-domain-systemd-entrypoint
COPY --chown=0:0 \
    tests/p1_dual_domain_rootful_systemd/hepta_p1_dual_domain_inner_gate.py \
    /usr/local/libexec/hepta_p1_dual_domain_inner_gate.py

RUN set -eu; \
    chmod 0755 \
        /usr/libexec/hepta-p1-dual-domain-daemon \
        /usr/local/libexec/hepta-p1-dual-domain-systemd-entrypoint \
        /usr/local/libexec/hepta_p1_dual_domain_inner_gate.py; \
    chmod 0644 \
        /usr/lib/systemd/system/hepta-p1-dual-domain-rootful.target \
        /usr/lib/systemd/system/hepta-p1-dual-watch@.service \
        /usr/lib/systemd/system/hepta-p1-dual-watch@.socket \
        /usr/lib/systemd/system/hepta-p1-dual-paper@.service \
        /usr/lib/systemd/system/hepta-p1-dual-paper@.socket; \
    [ ! -e /usr/libexec/hepta-ib-executiond ]; \
    [ ! -e /usr/lib/systemd/system/hepta-execution-ib-paper.service ]; \
    rm -rf /etc/systemd/system/*.wants/* \
        /etc/systemd/system/*.requires/* \
        /var/lib/systemd/random-seed /var/log/*; \
    ln -sfn \
        /usr/lib/systemd/system/hepta-p1-dual-domain-rootful.target \
        /etc/systemd/system/default.target

LABEL io.hepta.purpose="p1-dual-domain-rootful-gate" \
      io.hepta.network="none" \
      io.hepta.authority="watch-plus-inert-paper-all-false" \
      io.hepta.ib-api="absent" \
      io.hepta.orders="impossible"

STOPSIGNAL SIGRTMIN+3
HEALTHCHECK NONE
USER 0:0
WORKDIR /
ENTRYPOINT ["/usr/local/libexec/hepta-p1-dual-domain-systemd-entrypoint"]
CMD []
