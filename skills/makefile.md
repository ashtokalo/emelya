---
description: Writing and running Makefiles — targets, variables, patterns, debugging
---
# Makefiles

## Running make

- `make` — build the default (first) target
- `make <target>` — build specific target
- `make -n` — dry run, show commands without executing
- `make -j$(nproc)` — parallel build
- `make -p` — print database of rules and variables (for debugging)
- `make -B` — force rebuild all targets
- `make VAR=value` — override a variable from command line

## Common targets

Standard convention most projects follow:

```makefile
.PHONY: all clean test install lint

all: build          # default target, listed first

build:
	go build -o bin/app ./cmd/app

test:
	go test ./...

clean:
	rm -rf bin/ build/

install: build
	cp bin/app /usr/local/bin/

lint:
	golangci-lint run
```

Always declare `.PHONY` for targets that don't produce files (all, clean, test, etc.) to avoid conflicts with files of the same name.

## Variables

```makefile
CC ?= gcc               # ?= sets only if not already defined
CFLAGS := -Wall -Wextra  # := immediate assignment
SRC = $(wildcard src/*.c) # = recursive (lazy) assignment
OBJ = $(SRC:.c=.o)       # substitution reference
```

Override variables: `make CC=clang` from command line.

## Pattern rules

```makefile
%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@
```

Key automatic variables: `$@` (target), `$<` (first prerequisite), `$^` (all prerequisites), `$*` (stem matched by %).

## Debugging tips

- `make -n` — see what would run without executing
- `make -p | grep -A5 target` — inspect how a target is resolved
- `$(info VAR is $(VAR))` — print variable value during parsing
- `$(error stopping here)` — halt with a message

When editing Makefiles, use tabs for recipe indentation (not spaces). Use read tool to inspect existing Makefiles, edit tool for modifications, bash tool to run make commands.
