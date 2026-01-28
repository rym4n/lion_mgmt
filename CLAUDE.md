# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- **Install Dependencies**: `uv sync`
- **Run Server**: `uv run server.py`
- **Database Migrations**:
  - Generate migration: `aerich migrate`
  - Apply migration: `aerich upgrade`
- **Testing**: No formal test suite exists (tests directory is empty).

## Architecture

- **Framework**: Sanic (Python Async Web Framework)
- **Database**: PostgreSQL with Tortoise ORM
- **Caching**: Redis (aiocache)
- **Messaging**: Kafka (aiokafka)
- **Package Manager**: uv
- **Pattern**: Modular architecture with services isolated in `services/` directory.

## Project Structure

- `server.py`: Application entry point.
- `core/`: Framework infrastructure (global exception handlers, middlewares, response helpers).
- `libs/`: Shared utilities (security, kafka wrapper, common tools).
- `models/`: Tortoise ORM models (database schema).
- `services/`: Business logic modules (blueprints and views).
- `settings/`: Configuration loading and environment variables.
- `migrations/`: Aerich database migration versions.

## Development Standards

### Response Format
All API responses must use the unified format via `core.response`:
- Success: `return response_ok(data)`
- Failure: `raise CustomError(ErrorCode.Specific_Error)` or `return response_fail(ErrorCode)`

### Database & Models
- **ORM**: Use Tortoise ORM for all database interactions.
- **Soft Delete**: Always filter queries with `deleted=False`.
- **Multi-tenancy**: Ensure `tenant_id` is respected in queries where applicable.
- **Async**: Use `await` for all DB operations; use `in_transaction()` for atomicity.

### Naming Conventions
- **Files/Variables**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`
- **Routes**: `kebab-case` (e.g., `/user-profile`)

### Security & Permissions
- Routes require authentication by default via middleware.
- Use `@requires_permission("scope:action")` decorator for authorization.
- Access session data via `request.ctx.session`.

### Imports
1. Standard library
2. Third-party packages
3. Local imports (core, libs, models, services)
