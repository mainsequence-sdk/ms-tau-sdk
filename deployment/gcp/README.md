# GCP Deployment

Cloud Build receives the Astro monorepo:

```text
astro/
├── packages/
│   ├── tau-file-tools/
│   └── tau-web-access/
├── Dockerfile
└── pyproject.toml
```

Run the build from the Astro repository root:

```bash
gcloud builds submit --config deployment/gcp/cloudbuild.yaml .
```

The pipeline builds and publishes:

- `astro/astro-tau:astro-<version>`
- `astro/astro-tau:python-3.13`
- `astro/astro-tau:latest`
- `astro/code-repository-executor-bundle:astro-<version>`
- `astro/code-repository-executor-bundle:latest`

The Docker build step enables BuildKit because the trusted runtime Dockerfile
uses cache mounts while materializing its locked Python dependencies.

The main image is built from the digest-pinned official
`python:3.13-slim-bookworm` index and installs Astro into the single
`/opt/venv` runtime environment. It runs as `appuser` (`10000:10000`) with
`HOME=/home/appuser` and `/workspace` as its working directory.

The remote-worker Dockerfile accepts only a CodeRepository base implementing
that same ABI. It rejects a base whose active `python` is older than 3.13,
whose active prefix is not `/opt/venv`, or whose user/filesystem variables do
not match the contract. Astro and CodeRepository dependencies remain in that
one environment. The overlay keeps its immutable source bundle root-owned at
`/app`, verifies the Git checkout at `/workspace`, and uses `/session-state`
for mutable executor state. It does not install Node.js, a second interpreter,
or a compatibility path for the retired notebook image ABI.

CodeRepository base images must also provide `ffmpeg`, `ffprobe`, `git`, and
`rg`; the remote worker build rejects images missing those web/file-tool
runtime dependencies. The offline Astro install must also produce the
`yt-dlp` executable.

The published remote-worker recipe is provider-neutral. Its
`EXECUTOR_BUNDLE_IMAGE` is supplied by the backend as the exact digest-pinned
bundle mirror in the CodeRepositoryBranch's target tenancy registry; the recipe does
not construct or pull a central GCP image from `PROJECT_ID`.

The Kubernetes workload has one `astro` container. Session durability is
provided by Django's native Tau entry and runtime-lease APIs; there is no
checkpoint sidecar or shared session-state volume.
