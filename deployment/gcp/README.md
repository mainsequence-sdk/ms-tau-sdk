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

The remote-worker Dockerfile rejects any code repository base image whose active
`python` is older than 3.13. Astro and code repository dependencies are installed into
that same environment. It does not install Node.js or a second interpreter.
CodeRepository base images must also provide `ffmpeg`, `ffprobe`, and `git`; the remote
worker build rejects images missing those web/tool runtime dependencies.

The published remote-worker recipe is provider-neutral. Its
`EXECUTOR_BUNDLE_IMAGE` is supplied by the backend as the exact digest-pinned
bundle mirror in the CodeRepositoryBranch's target tenancy registry; the recipe does
not construct or pull a central GCP image from `PROJECT_ID`.

The Kubernetes workload has one `astro` container. Session durability is
provided by Django's native Tau entry and runtime-lease APIs; there is no
checkpoint sidecar or shared session-state volume.
