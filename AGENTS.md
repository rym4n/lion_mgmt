# AGENTS.md - Lion Management Service

> Guidelines for AI coding agents working in this repository.

## Project Overview

Lion Management Service (AIOE) - A Python async web service built with Sanic framework, using Tortoise ORM for PostgreSQL, Redis for caching, and Kafka for messaging.

**Tech Stack:**
- Python 3.11+
- Sanic 24.12 (async web framework)
- Tortoise ORM with asyncpg (PostgreSQL)
- aiocache (Redis caching)
- aiokafka (message queue)
- JWT authentication with MFA support
- uv package manager

## Build/Lint/Test Commands

```bash
# Install dependencies
uv sync

# Run the server (development)
python server.py

# Database migrations (Aerich)
aerich migrate          # Generate migration
aerich upgrade          # Apply migrations

# No formal test suite exists - tests/ directory only contains __pycache__
# Consider adding pytest for testing
```

## Project Structure

```
lion_mgmt/
├── server.py              # Entry point - creates and runs Sanic app
├── settings/              # Configuration (env vars, DB, cache, logging)
│   └── __init__.py        # All settings defined here
├── core/                  # Framework infrastructure
│   ├── error_code.py      # ErrorCode enum definitions
│   ├── response.py        # response_ok(), response_fail() helpers
│   ├── exception_handlers/# Global exception handling
│   ├── listeners/         # Sanic lifecycle hooks
│   └── middlewares/       # Request/response middleware (auth, security)
├── models/                # Tortoise ORM models
│   ├── base.py            # BaseModel with id, ctime, utime, deleted, tenant_id
│   ├── user.py            # Account, Role, Tenant models
│   └── ...
├── services/              # Business logic (blueprints)
│   ├── __init__.py        # create_app() and blueprint registration
│   └── <module>/views.py  # Route handlers per module
├── libs/                  # Shared utilities
│   ├── security/          # authentication.py, authorization.py
│   ├── error_code/        # BaseErrorCode, CustomError exception
│   ├── kafka.py           # Kafka producer wrapper
│   └── utils.py           # Common utilities
└── migrations/            # Aerich migration files
```

## Code Style Guidelines

### Imports

```python
# coding=utf-8  # Always include at top of file

# Standard library first
import uuid
from datetime import datetime

# Third-party
from sanic import Blueprint
from tortoise.exceptions import DoesNotExist

# Local imports last
from core.error_code import ErrorCode
from core.response import response_ok, response_fail
from libs.error_code.exception import CustomError
from models import Account, Role
```

### Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Files/folders | snake_case | `user_views.py`, `event_ingestion/` |
| Variables/functions | snake_case | `get_client_ip()`, `tenant_id` |
| Classes | PascalCase | `SecurityMiddleware`, `BaseModel` |
| Constants | UPPER_SNAKE_CASE | `AUTHENTICATION_SESSION_EXPIRATION` |
| Route URLs | kebab-case | `/user-profile`, `/new-token` |
| Blueprint names | lowercase | `user_bp = Blueprint("user", url_prefix="/user")` |

### Type Hints

Always use type hints for function signatures and class attributes:

```python
from typing import Any, Optional

async def get_user(user_id: str) -> Optional[Account]:
    ...

class Account(BaseModel):
    username: str = fields.CharField(max_length=32)
    disabled: bool = fields.BooleanField(default=False)
```

### Response Format

All API responses use unified format via `core.response`:

```python
from core.response import response_ok, response_fail
from core.error_code import ErrorCode

# Success response: {"code": 0, "data": ...}
return response_ok({"id": user.id, "username": user.username})

# Error response: {"code": 4xxx, "message": "..."}
raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
# Or directly:
return response_fail(ErrorCode.PARAM_ERROR, status=400)
```

### Error Handling

Define errors in `core/error_code.py` using `ErrorData`:

```python
from libs.error_code.enum import BaseErrorCode, ErrorData

class ErrorCode(BaseErrorCode):
    ACCOUNT_NOT_FOUND = ErrorData(4009, "账号不存在")
    CUSTOM_ERROR = ErrorData(4100, "Custom error message")
```

Raise errors using `CustomError`:

```python
from libs.error_code.exception import CustomError

if not user:
    raise CustomError(ErrorCode.ACCOUNT_NOT_FOUND)
```

### Route Handlers

```python
from sanic import Blueprint
from sanic_ext import openapi
from libs.security.authorization import requires_permission

bp = Blueprint("module", url_prefix="/module")

@bp.post("/action")  # POST preferred for business operations
@openapi.definition(summary="Action description", ...)
@requires_permission("module:c")  # c=create, r=read, u=update, d=delete
async def action_handler(request):
    data = request.json or {}
    # Validate input
    if not data.get("required_field"):
        raise CustomError(ErrorCode.PARAM_ERROR)

    # Business logic
    result = await Model.create(**data)
    return response_ok(result.json)
```

### Database Models

Extend `BaseModel` for standard fields:

```python
from models.base import BaseModel
from tortoise import fields

class MyModel(BaseModel):
    name: str = fields.CharField(max_length=255)

    @property
    def json(self) -> dict:
        return {
            "id": self.id,
            "ctime": strip_timezone_to_seconds(self.ctime),
            "name": self.name,
        }

    class Meta:
        table = "my_model"
        ordering = ["-utime"]
```

### Async Database Operations

```python
# Always use async ORM operations
user = await Account.get_or_none(id=user_id, deleted=False)
users = await Account.filter(deleted=False).prefetch_related("roles").all()

# Use transactions for multi-step operations
from tortoise.transactions import in_transaction
async with in_transaction():
    await account.save()
    await account.roles.add(*roles)
```

### Authentication & Authorization

```python
# Middleware handles auth automatically for non-whitelisted routes
# White-listed routes in core/middlewares/security.py

# Permission decorator pattern
@requires_permission("user:r")  # Read permission
@requires_permission("user:c")  # Create permission
@requires_permission("user:u")  # Update permission
@requires_permission("user:d")  # Delete permission

# Access session data
user_id = request.ctx.session["sub"]
account = request.ctx.session["account_info"]
tenant_id = request.ctx.session["tenant_id"]
```

### Pagination Pattern

```python
req_json = request.json or {}
page = max(1, int(req_json.get("page", 1) or 1))
size = max(1, int(req_json.get("size", 15) or 15))
offset = (page - 1) * size

qs = Model.filter(deleted=False, tenant_id=tenant_id)
total = await qs.count()
items = await qs.offset(offset).limit(size).all()

return response_ok({
    "total": total,
    "page": page,
    "size": size,
    "total_pages": (total + size - 1) // size,
    "data": [item.json for item in items],
})
```

## SecOpsExpert Agent

Integrated intelligent agent for security operations.

**Features:**
- 5-layer memory architecture (L1-L5) using Agent
- Autonomous playbook execution
- Warroom integration (auto-trigger, chat)
- Knowledge base with vector search (pgvector)

**Configuration:**
See `settings/__init__.py` for Agent and Embedding config.

## Key Patterns

1. **Soft Delete**: Use `deleted=False` filter, never hard delete
2. **Multi-tenancy**: Filter by `tenant_id` from session
3. **Caching**: Use `request.app.ctx.cache_client` for Redis operations
4. **Audit Logging**: Handled automatically by SecurityMiddleware via Kafka
5. **OpenAPI Docs**: Use `@openapi.definition()` decorator for API documentation

## Common Pitfalls

- Always check `deleted=False` when querying
- Use `prefetch_related()` for ManyToMany fields before accessing them
- Session data is in `request.ctx.session`, not `request.session`
- All timestamps are timezone-aware; use `settings.TZ` for conversions
- Environment variables are loaded from `settings/.env` in DEV mode
