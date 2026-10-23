# Source Code Publication Checklist

This repository currently distributes a Windows application. **A packaged `.exe` is not a substitute for publishing the application's original source code.** This checklist is for maintainers preparing a source release.

## 1. Find the real development project

- [ ] Locate the development root containing editable program files and project configuration (for example, source files and dependency manifests), not only the packaged output directory.
- [ ] Do not treat a folder such as `output/final_delivery/dist/` or its executable files as the original source tree.
- [ ] Confirm which directories are essential to build and run the project.
- [ ] Keep a local backup before changing or publishing anything.

Common source file types may include `.py`, `.js`, `.ts`, `.html`, `.css`, `.json`, `.toml`, `.yaml` and `.yml`, but file extensions alone do not prove that a file is safe to publish.

## 2. Audit and clean the source tree

- [ ] Remove real patient information, unpublished research data and confidential image sets.
- [ ] Remove passwords, API keys, tokens, `.env` files, certificates and local configuration containing secrets.
- [ ] Exclude local databases, inspection history, caches, logs, virtual environments and build artifacts.
- [ ] Review third-party code, model files, fonts and other bundled dependencies and their redistribution licenses.
- [ ] Replace private test inputs with synthetic or explicitly redistributable demonstration files.
- [ ] Use `.gitignore` before uploading; confirm sensitive files are not already tracked. `.gitignore` alone cannot remove a secret from existing Git history.

## 3. Make the source reproducible

- [ ] Include dependency information and exact versions, where applicable.
- [ ] Write real setup and build commands based on the project's actual implementation; do not invent commands.
- [ ] Test from a clean checkout on a separate Windows machine or profile.
- [ ] Confirm that any published source and binary can be associated with a documented version/tag.
- [ ] Add appropriate licensing and copyright notices for the source and any third-party components.

## 4. Publish

- [ ] Add the reviewed source directories and configuration files to the repository, ideally using Git for a multi-file project.
- [ ] Commit source with a clear message, such as `Publish initial application source code`.
- [ ] Update README sections that currently say source code is unavailable.
- [ ] Link the documented build procedure and changelog.
- [ ] Do not claim that the source reproduces an existing binary until a build comparison or equivalent verification has been completed.

## Maintainer reminder

An MIT license file in a repository does not automatically grant the right to relicense or redistribute third-party software bundled with the application. Check all components before making a complete source release.
