"""Canonical state for explicitly inactive or invalid configured routes."""

DOWN_ROUTE_STATES = frozenset({'down', 'inactive', 'invalid', 'disabled'})


def route_is_down(value):
    return str(value).strip().lower() in DOWN_ROUTE_STATES


def normalize_route_state(value):
    return 'down' if route_is_down(value) else value
