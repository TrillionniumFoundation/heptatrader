ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER 0:0
WORKDIR /
ENV LD_PRELOAD="" LD_AUDIT="" LD_LIBRARY_PATH="" PYTHONPATH="" PYTHONHOME=""
SHELL ["/bin/sh", "-ec"]

RUN for path in /usr/bin/python3 /usr/bin/passwd /usr/sbin/nft \
        /usr/sbin/groupadd /usr/sbin/useradd; do \
        [ -x "$path" ] || exit 69; \
    done

RUN for item in \
        hepta-gateway:2001 hepta-exec:2002 hepta-ib-exec:2003 \
        hepta-agent:2004 hepta-gw-codex-a:2101 \
        hepta-gw-openclaw-b:2102 hepta-agent-codex-a:2104 \
        hepta-agent-openclaw-b:2105 hepta-exec-codex-a:2111 \
        hepta-exec-openclaw-b:2112 hepta-ib-exec-codex-a:2121 \
        hepta-ib-exec-openclaw-b:2122; do \
        name="${item%:*}"; id="${item#*:}"; \
        groupadd --gid "$id" "$name"; \
        useradd --uid "$id" --gid "$id" --no-create-home \
            --home-dir /nonexistent --shell /usr/sbin/nologin "$name"; \
        passwd --lock "$name" >/dev/null; \
    done

COPY --chown=0:0 hepta_broker_egress_policy.py \
    /usr/libexec/hepta-broker-egress-policy
COPY --chown=0:0 hepta_ib_paper_domain_authority.py \
    /usr/libexec/hepta-ib-paper-domain-authority
COPY --chown=0:0 hepta-broker-network-policy-v1.json \
    /usr/share/heptatrader/hepta-broker-network-policy-v1.json
COPY --chown=0:0 hepta-service-identities-v1.json \
    /usr/share/heptatrader/hepta-service-identities-v1.json
COPY --chown=0:0 hepta_broker_network_opt_in_gate.py \
    /usr/local/libexec/hepta_broker_network_opt_in_gate.py

RUN chmod 0755 /usr/libexec/hepta-broker-egress-policy \
        /usr/libexec/hepta-ib-paper-domain-authority \
        /usr/local/libexec/hepta_broker_network_opt_in_gate.py; \
    chmod 0644 \
        /usr/share/heptatrader/hepta-broker-network-policy-v1.json \
        /usr/share/heptatrader/hepta-service-identities-v1.json; \
    [ ! -e /usr/libexec/hepta-ib-executiond ]; \
    [ ! -e /usr/lib/systemd/system/hepta-execution-ib-paper.service ]; \
    [ ! -e /etc/heptatrader/credentials ]

ENTRYPOINT ["/usr/bin/python3", "/usr/local/libexec/hepta_broker_network_opt_in_gate.py"]
