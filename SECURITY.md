# Security and privacy

## Data handling

The plugin keeps project state and incremental caches inside the project selected for the current run. It does not intentionally use one project's generated content as memory for another project.

Image generation sends the current page's sealed prompt and task-local reference images to Codex Images. The allowlisted QA and visual reconstruction gateways send the generated body image, authoritative Word content, normalized style context, and relevant page-local image pixels to the built-in OpenAI HTTPS endpoint. Both gateways use hard timeouts and project-local signed provenance. Review your organization's data policy before processing confidential documents.

The repository and release packages must not contain API keys, Codex authentication files, user documents, generated presentations, project caches, or machine-specific user paths.

## Credentials

Do not commit credentials. Configure Image2 through Codex login and provide `OPENAI_API_KEY` through the process environment for QA/reconstruction. Keys are never written into project artifacts, caches, logs, manifests, or release packages. The installer never asks users to paste a token into this repository.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting feature for this repository. Do not include real confidential documents or credentials in a report; use a minimal synthetic reproduction.

## Supported release

Security fixes target the latest published release. Older cachebuster builds are not maintained.
