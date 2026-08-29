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
        /usr/sbin/ip \
        /usr/sbin/nft \
        /usr/sbin/groupadd \
        /usr/sbin/useradd; do \
        [ -x "$path" ] || exit 69; \
    done

RUN groupadd --gid 2101 hepta-gw-codex-a; \
    groupadd --gid 2102 hepta-gw-openclaw-b; \
    groupadd --gid 2121 hepta-ib-exec-codex-a; \
    groupadd --gid 2122 hepta-ib-exec-openclaw-b; \
    useradd --uid 2101 --gid 2101 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-gw-codex-a; \
    useradd --uid 2102 --gid 2102 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin hepta-gw-openclaw-b; \
    useradd --uid 2121 --gid 2121 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-ib-exec-codex-a; \
    useradd --uid 2122 --gid 2122 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin \
        hepta-ib-exec-openclaw-b; \
    passwd --lock hepta-gw-codex-a >/dev/null; \
    passwd --lock hepta-gw-openclaw-b >/dev/null; \
    passwd --lock hepta-ib-exec-codex-a >/dev/null; \
    passwd --lock hepta-ib-exec-openclaw-b >/dev/null

COPY --chown=0:0 install-root/usr/ /usr/
COPY --chown=0:0 provision-root/ /opt/hepta-paper-gate/provision/
COPY --chown=0:0 \
    tests/paper_domain_rootful_systemd/hepta-paper-domain-systemd-entrypoint \
    /usr/local/libexec/hepta-paper-domain-systemd-entrypoint
COPY --chown=0:0 \
    tests/paper_domain_rootful_systemd/hepta_paper_domain_rootful_inner_gate.py \
    /usr/local/libexec/hepta_paper_domain_rootful_inner_gate.py
COPY --chown=0:0 \
    tests/paper_domain_rootful_systemd/hepta-paper-domain-rootful-systemd.target \
    /usr/lib/systemd/system/hepta-paper-domain-rootful-systemd.target

RUN set -eu; \
    chmod 0755 \
        /usr/libexec/hepta-broker-egress-policy \
        /usr/libexec/hepta-ib-paper-domain-authority \
        /usr/libexec/hepta-ib-executiond \
        /usr/local/libexec/hepta-paper-domain-systemd-entrypoint \
        /usr/local/libexec/hepta_paper_domain_rootful_inner_gate.py; \
    chmod 0644 /usr/lib/systemd/system/*.service \
        /usr/lib/systemd/system/*.socket \
        /usr/lib/systemd/system/hepta-paper-domain-rootful-systemd.target; \
    rm -rf /etc/systemd/system/*.wants/* \
        /etc/systemd/system/*.requires/* \
        /var/lib/systemd/random-seed /var/log/*; \
    ln -sfn \
        /usr/lib/systemd/system/hepta-paper-domain-rootful-systemd.target \
        /etc/systemd/system/default.target

LABEL io.hepta.purpose="paper-domain-rootful-systemd-gate" \
      io.hepta.network="none" \
      io.hepta.ib-api="absent" \
      io.hepta.paper-daemon="inert-stub"

STOPSIGNAL SIGRTMIN+3
HEALTHCHECK NONE
USER 0:0
WORKDIR /
ENTRYPOINT ["/usr/local/libexec/hepta-paper-domain-systemd-entrypoint"]
CMD []
