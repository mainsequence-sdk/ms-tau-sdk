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
- `astro/project-executor-bundle:astro-<version>`
- `astro/project-executor-bundle:latest`

The remote-worker Dockerfile rejects any project base image whose active
`python` is older than 3.13. Astro and project dependencies are installed into
that same environment. It does not install Node.js or a second interpreter.
Project base images must also provide `ffmpeg`, `ffprobe`, and `git`; the remote
worker build rejects images missing those web/tool runtime dependencies.

The Kubernetes workload has one `astro` container. Session durability is
provided by Django's native Tau entry and runtime-lease APIs; there is no
checkpoint sidecar or shared session-state volume.
