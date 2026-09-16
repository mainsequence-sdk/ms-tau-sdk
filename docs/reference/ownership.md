# Ownership and Release Authority

Main Sequence TAU SDK maintainers own this repository's Python package, public construction API,
command, packaged resources, documented settings, tested wire contracts, CI, and Python release
artifacts.

Consuming-project owners choose and lock the SDK version, provide the project workspace and Python
environment, own `.tau` configuration and extensions, and build or deploy their own project
artifact. External Main Sequence service owners retain authority over their APIs, credential
issuance, persistence, and deployment systems. This SDK repository does not modify those systems.

Repository maintainers may merge compatible SDK changes after the quality gates pass. A release
owner additionally reviews the changelog and compatibility impact, confirms the fixture lock and
clean-install evidence, configures/approves the protected `pypi` environment, and explicitly creates
the exact `v<version>` tag. Build scripts and ordinary development commits never create or push a
release tag.

Project extensions remain the consuming project's responsibility. The SDK maintainers do not
review, license, secure, or operate extensions merely because Tau loads them from a project
workspace.
