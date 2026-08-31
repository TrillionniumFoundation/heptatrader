# Third-party and vendor boundary notices

This file records the distribution boundary for the repository. It is not a
substitute for the original third-party licenses and does not grant rights that
the project does not possess.

## Core release package

The default `HEPTA_ENABLE_IBAPI=OFF` package contains HeptaTrader runtime
binaries, Python adapters, systemd definitions, policies, and documentation. It
does not intentionally package broker credentials, account configuration, IB
API source or binaries, CTP binaries/headers, XT/QMT binaries, or market data.

OpenSSL is resolved as a system build dependency. Its runtime licensing and
redistribution obligations are controlled by the operating-system package and
the OpenSSL project terms. The generated SPDX SBOM records installed artifacts,
not a claim that every system library is redistributed by HeptaTrader.

## Interactive Brokers API

IB-enabled builds require an operator-supplied `IBAPI_ROOT`. The SDK is not
fetched or redistributed by this repository. The operator is responsible for
obtaining it from an authorized source, pinning the reviewed version, and
complying with Interactive Brokers terms. IB artifacts are excluded from the
default public package and must pass the controlled IB PAPER qualification
workflow before use.

## CTP 6.7.7 boundary

`third_party/ctp/6.7.7/` describes a separately controlled overlay. The original
source URL and independent redistribution authorization were not preserved by
the legacy import. Distribution authorization therefore remains false. The CTP
adapter in the public build fails closed and the default package excludes the
vendor overlay and binaries.

## XT / QMT

XT/QMT documentation describes interface semantics observed in an operator
installation. No XT/QMT SDK, binary extension, DLL, account authorization, or
real outbound transport is distributed. The checked-in adapter is an
unsupported event-normalization scaffold and rejects all outbound operations.

## Legacy source-only trees

The repository contains legacy TinyXML and oneTBB-compatible headers under
`Interface/`. They are not installed by the default core package and are not
part of the canonical Agent OS runtime. Before any source redistribution or
legacy-monolith release, preserve the upstream license texts, verify origin and
version, and generate a component-level SBOM. Absence of an upstream license
file in this repository must be treated as a release blocker, not as permission.
