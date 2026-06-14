---
description: C/C++ development — compilation, make/cmake, debugging, memory analysis
---
# C/C++ Development

## Compilation

- `gcc -Wall -Wextra -o output main.c` — compile C with warnings
- `g++ -Wall -Wextra -std=c++17 -o output main.cpp` — compile C++ with C++17
- `clang -Wall -Wextra -o output main.c` — alternative compiler (often better diagnostics)
- Add `-g` for debug symbols, `-O2` for optimization, `-fsanitize=address` for AddressSanitizer

Key warning flags: `-Wall -Wextra -Wpedantic -Werror` (treat warnings as errors in CI).

## Make

- `make` — build default target
- `make clean` — remove build artifacts
- `make -j$(nproc)` — parallel build using all cores
- `make -n` — dry run, show commands without executing

Check for `Makefile` or `GNUmakefile` with read tool to understand build targets.

## CMake

- `cmake -B build -DCMAKE_BUILD_TYPE=Debug` — configure into build/
- `cmake --build build -j$(nproc)` — build
- `ctest --test-dir build --output-on-failure` — run tests
- `cmake --install build --prefix /usr/local` — install

Check `CMakeLists.txt` with read tool for project structure and targets.

## Debugging with GDB

- `gdb ./program` — start debugger
- Common commands: `run`, `break main`, `next`, `step`, `print var`, `backtrace`, `quit`
- `gdb -batch -ex run -ex backtrace ./program` — non-interactive crash backtrace

## Memory analysis

- `valgrind --leak-check=full ./program` — detect memory leaks
- `valgrind --tool=memcheck --track-origins=yes ./program` — trace uninitialized values
- Compile with `-g -O0` for best valgrind output

## Project structure

Typical layout:
- `src/` — source files (.c/.cpp)
- `include/` — header files (.h/.hpp)
- `tests/` — test files
- `build/` — build artifacts (gitignored)
- `Makefile` or `CMakeLists.txt` — build config

Use bash tool for all commands. Use read/edit tools for source and config files.
