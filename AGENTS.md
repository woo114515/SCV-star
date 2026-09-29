# Repository Guidelines

## Project Structure & Module Organization

This directory is currently empty: no source code, tests, assets, or dependency manifests are present. When adding the initial implementation, organize application code under `src/`, automated tests under `tests/`, and static resources under `assets/` where the chosen framework permits. Keep project configuration at the root and document the actual layout in `README.md`. Avoid creating unused directories.

## Build, Test, and Development Commands

No build, test, or local development commands are configured yet. The first implementation should provide reproducible commands for installing dependencies, running locally, building, and testing. Record the exact commands and required runtime versions in `README.md`; do not assume commands such as `npm test` apply until their configuration exists. Commit the appropriate dependency lockfile when introducing a package manager.

## Coding Style & Naming Conventions

No language, formatter, or linter has been selected. Follow the chosen language’s standard conventions and configure its formatter and linter alongside the first source files. Use consistent indentation within each file, descriptive identifiers, and filenames that match their primary responsibility. Prefer small modules with explicit interfaces. Keep formatting changes separate from unrelated functional changes.

## Testing Guidelines

No testing framework or coverage threshold exists. Introduce tests with new behavior and document how to execute them. Use descriptive test names that identify the behavior and expected result, following the selected framework’s discovery rules. Cover relevant failure paths and add regression tests for bug fixes.

## Commit & Pull Request Guidelines

No Git metadata is present, so existing commit conventions cannot be inferred. Once version control is initialized, use concise, imperative commit subjects, such as `Add initial project scaffold`. Keep commits focused. Pull requests should explain the change, link related issues when available, and list validation performed. Include screenshots for visible interface changes and disclose any checks that could not be run.

## Security & Configuration

Keep credentials and local environment files out of version control. Provide placeholder configuration examples and document required settings when introducing external services.
