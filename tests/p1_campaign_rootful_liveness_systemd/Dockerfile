ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG BASE_IMAGE

USER 0:0
WORKDIR /
ENV LD_PRELOAD="" LD_AUDIT="" LD_LIBRARY_PATH="" PYTHONPATH="" \
    PYTHONHOME="" BASH_ENV="" ENV=""
SHELL ["/bin/sh", "-ec"]

RUN digest="${BASE_IMAGE##*@sha256:}"; \
    [ "$digest" != "$BASE_IMAGE" ]; \
    [ "${#digest}" -eq 64 ]; \
    case "$digest" in *[!0-9a-f]*) exit 64;; esac; \
    for path in /usr/lib/systemd/systemd /usr/bin/systemctl \
        /usr/bin/python3 /usr/bin/install /usr/bin/mount /usr/bin/findmnt; do \
        [ -x "$path" ] || exit 69; \
    done

COPY --chown=0:0 install-root/usr/ /usr/
COPY --chown=0:0 production-unit-inputs/ /opt/hepta-inputs/systemd/
COPY --chown=0:0 \
    tests/p1_campaign_rootful_liveness_systemd/hepta-p1-liveness-systemd-entrypoint \
    /usr/local/libexec/hepta-p1-liveness-systemd-entrypoint
COPY --chown=0:0 \
    tests/p1_campaign_rootful_liveness_systemd/hepta_p1_liveness_inner_gate.py \
    /usr/local/libexec/hepta_p1_liveness_inner_gate.py

RUN set -eu; \
    for path in \
        /usr/libexec/hepta-p1-safety-soak-campaign-coordinator \
        /usr/libexec/hepta-p1-safety-soak-observer-worker \
        /usr/libexec/hepta-p1-safety-soak-recorder-worker; do \
        printf '%s\n' '#!/bin/sh' 'exit 125' > "$path"; \
        chmod 0755 "$path"; \
    done; \
    chmod 0755 /usr/libexec/hepta-p1-liveness-daemon \
        /usr/local/libexec/hepta-p1-liveness-systemd-entrypoint \
        /usr/local/libexec/hepta_p1_liveness_inner_gate.py; \
    chmod 0644 /usr/lib/systemd/system/hepta-p1-liveness-watchdog.service \
        /usr/lib/systemd/system/hepta-p1-liveness-worker.service \
        /usr/lib/systemd/system/hepta-p1-liveness-coordinator.service \
        /usr/lib/systemd/system/hepta-p1-campaign-rootful-liveness.target; \
    test -f /opt/hepta-inputs/systemd/hepta-p1-safety-soak-campaign@.service; \
    test -f /opt/hepta-inputs/systemd/hepta-p1-safety-soak-observer-worker@.service; \
    test -f /opt/hepta-inputs/systemd/hepta-p1-safety-soak-recorder-worker@.service; \
    test -f /opt/hepta-inputs/systemd/hepta-p1-safety-soak@.target; \
    [ ! -e /usr/libexec/hepta-ib-executiond ]; \
    [ ! -e /usr/lib/systemd/system/hepta-execution-ib-paper.service ]; \
    rm -rf /etc/systemd/system/*.wants/* \
        /etc/systemd/system/*.requires/* \
        /var/lib/systemd/random-seed /var/log/*; \
    ln -sfn \
        /usr/lib/systemd/system/hepta-p1-campaign-rootful-liveness.target \
        /etc/systemd/system/default.target

LABEL io.hepta.purpose="p1-campaign-rootful-liveness-gate" \
      io.hepta.network="none" \
      io.hepta.authority="all-false" \
      io.hepta.ib-api="absent" \
      io.hepta.orders="impossible"

STOPSIGNAL SIGRTMIN+3
HEALTHCHECK NONE
USER 0:0
WORKDIR /
ENTRYPOINT ["/usr/local/libexec/hepta-p1-liveness-systemd-entrypoint"]
CMD []
