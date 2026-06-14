---
description: Design principles — SOLID, DRY/KISS/YAGNI, patterns, code smells, refactoring
---
# Design Principles

## Core principles

- **KISS** — Keep it simple. Choose the straightforward solution over the clever one
- **YAGNI** — Don't build features until they're actually needed
- **DRY** — Don't repeat yourself, but don't abstract prematurely either. Rule of three: duplicate twice before extracting

## SOLID (object-oriented)

- **S** — Single Responsibility: a class/module has one reason to change
- **O** — Open/Closed: extend behavior without modifying existing code
- **L** — Liskov Substitution: subtypes must be usable wherever the parent type is expected
- **I** — Interface Segregation: prefer small, focused interfaces over large ones
- **D** — Dependency Inversion: depend on abstractions, not concretions

## Common patterns (one-liners)

- **Factory** — centralize object creation, hide construction details
- **Strategy** — swap algorithms at runtime via a common interface
- **Observer** — notify subscribers when state changes (event systems)
- **Decorator** — add behavior to objects without modifying their class
- **Adapter** — make incompatible interfaces work together
- **Builder** — construct complex objects step by step
- **Repository** — abstract data access behind a collection-like interface

## When to use

- **Factory**: multiple types share an interface, creation logic varies
- **Strategy**: behavior varies by context and you're seeing if/else chains picking algorithms
- **Observer**: one change must notify multiple independent components
- **Decorator**: you need optional, composable behavior layers
- **Adapter**: integrating third-party code or legacy APIs

## Code smells (refactoring triggers)

- **Long method** (>30 lines) — extract smaller functions
- **Large class** — split by responsibility
- **Duplicated logic** — extract shared function or module
- **Deep nesting** (>3 levels) — use early returns, extract helpers
- **Feature envy** — method uses another class's data more than its own; move it
- **Shotgun surgery** — one change requires edits across many files; consolidate
- **Primitive obsession** — using strings/ints where a value object would add clarity
- **Boolean parameters** — usually means the function does two things; split it

## Refactoring approach

1. Ensure tests exist covering the code to be changed
2. Make one small, behavior-preserving change at a time
3. Run tests after each change
4. Commit frequently so each step is reversible

Never refactor and change behavior in the same commit.
