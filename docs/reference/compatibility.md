# Compatibility Policy

The `0.x` series is the initial development contract. Minor releases may refine private modules and
settings when accompanied by migration notes. The public `ms_tau_sdk.create_app`,
`ms_tau_sdk.TauSDKSettings`, `ms-tau` command, documented environment names, and tested HTTP/wire
contracts are intentional surfaces.

Tau is pinned exactly because its resource precedence, extension lifecycle, provider catalog,
thinking levels, and storage protocol directly affect SDK behavior. Updating Tau requires the full
configuration, provider, persistence, distribution, and consumer-fixture gates.

The first `1.0.0` release requires a published release candidate, validated artifacts, current
documentation, and an explicit compatibility review. Private module imports are unsupported unless
a later ADR promotes them.
