---
description: Go development — modules, build, test, linting, project layout
---
# Go Development

## Modules and dependencies

- `go mod init <module-path>` — initialize new module
- `go mod tidy` — add missing and remove unused dependencies
- `go get <package>@latest` — add or update a dependency
- `go mod download` — download dependencies without building

Check `go.mod` and `go.sum` with read tool to understand current dependencies.

## Building and running

- `go run .` — compile and run current package
- `go run ./cmd/myapp` — run specific command
- `go build -o bin/myapp ./cmd/myapp` — compile binary to bin/
- `go install ./...` — install all packages

## Testing

- `go test ./...` — test all packages
- `go test -v -run TestName ./pkg/foo` — run specific test with verbose output
- `go test -count=1 ./...` — skip test cache
- `go test -race ./...` — detect data races
- `go test -cover ./...` — show coverage percentage
- `go test -coverprofile=coverage.out ./... && go tool cover -html=coverage.out` — coverage report

## Linting and formatting

- `go fmt ./...` — format all code (always run before committing)
- `go vet ./...` — catch common mistakes (shadows, printf mismatches, etc.)
- `staticcheck ./...` — extended static analysis (preferred over golint, which is deprecated)
- `golangci-lint run` — meta-linter running multiple checks at once

## Project layout

Standard layout for non-trivial projects:
- `cmd/myapp/main.go` — entry points (one per binary)
- `internal/` — private packages (not importable by other modules)
- `pkg/` — public library code (optional, some projects skip this)
- `go.mod`, `go.sum` — module definition and checksums

Check for `Makefile`, `.golangci.yml`, or `goreleaser.yml` with read tool to understand build/lint config. Use bash tool for all commands.
