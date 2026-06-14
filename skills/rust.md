---
description: Rust development — cargo workflows, linting, formatting, project structure
---
# Rust Development

## Creating and managing projects

- `cargo new myproject` — create new binary project
- `cargo new mylib --lib` — create new library project
- `cargo init` — initialize in existing directory

Check `Cargo.toml` with read tool to understand dependencies and project config.

## Building and running

- `cargo build` — compile (debug mode)
- `cargo build --release` — compile with optimizations
- `cargo run` — build and run
- `cargo run -- arg1 arg2` — pass arguments to the binary
- `cargo run --bin mybin` — run specific binary in workspace

## Testing

- `cargo test` — run all tests
- `cargo test test_name` — run tests matching name
- `cargo test -- --nocapture` — show println output during tests
- `cargo test -p my_crate` — test specific crate in workspace
- `cargo bench` — run benchmarks

## Linting and formatting

- `cargo fmt` — format all code (always run before committing)
- `cargo fmt -- --check` — check formatting without modifying
- `cargo clippy` — lint for common mistakes and style issues
- `cargo clippy -- -D warnings` — treat clippy warnings as errors
- `cargo clippy --fix` — auto-fix what it can

## Dependency management

- `cargo add <crate>` — add dependency (Cargo.toml)
- `cargo add <crate> --dev` — add dev dependency
- `cargo update` — update dependencies within semver constraints
- `cargo tree` — display dependency tree
- `cargo audit` — check for known vulnerabilities (requires cargo-audit)

## Project structure

- `src/main.rs` — binary entry point
- `src/lib.rs` — library root
- `src/bin/` — additional binaries
- `tests/` — integration tests
- `benches/` — benchmarks
- `Cargo.toml` — manifest (dependencies, metadata)
- `Cargo.lock` — exact dependency versions (commit for binaries, gitignore for libraries)

Check for `rust-toolchain.toml`, `.cargo/config.toml`, or workspace `Cargo.toml` with read tool for project-wide settings. Use bash tool for all commands.
