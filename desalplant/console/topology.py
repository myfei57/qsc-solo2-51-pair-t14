"""Console handlers for equipment topology registration and migration."""

from typing import Any

from ..errors import InvalidTopologyError
from ..topology.auth import Principal
from . import routes
from .http import Request, Response, error_response, json_response


def _registry(console):
    return console.plant.topology


def _principal(request: Request) -> Principal:
    return Principal.from_header(
        request.header("X-Topology-Roles"),
        request.header("X-Topology-User"),
    )


def _expected_version(request: Request, body: dict[str, Any]):
    raw = request.param("expected_version", body.get("expected_version"))
    if raw is None:
        return None
    return int(raw)


def current(console, request: Request) -> Response:
    """Return the active topology registration."""
    return json_response(_registry(console).current())


def versions(console, request: Request) -> Response:
    """List immutable topology versions."""
    return json_response({"versions": list(_registry(console).versions())})


def version(console, request: Request) -> Response:
    """Return one immutable topology version."""
    return json_response(_registry(console).version(request.integer("version")))


def resolve(console, request: Request) -> Response:
    """Resolve an identifier according to a requested historical version."""
    identifier = request.param("id", "")
    if not identifier:
        return error_response("id query parameter is required", status=400)
    at_version = request.integer("version") or None
    return json_response(_registry(console).resolve(identifier, at_version))


def replacement(console, request: Request) -> Response:
    """Show who replaced a historical identifier and at which version."""
    old_id = request.param("old_id", "")
    if not old_id:
        return error_response("old_id query parameter is required", status=400)
    return json_response(_registry(console).replacement(old_id))


def audit(console, request: Request) -> Response:
    """Topology validation conclusions, commits and denials."""
    return json_response(
        {"events": list(_registry(console).audit_events(request.integer("limit", 100)))}
    )


def register(console, request: Request) -> Response:
    """Validate the complete batch and atomically activate it when accepted."""
    body = request.body()
    dry_run = request.param("dry_run", "") in ("1", "true", "yes") or bool(body.get("dry_run"))
    batch = body.get("batch", body)
    if not isinstance(batch, dict):
        return error_response("topology batch must be a JSON object", status=400)
    try:
        result = _registry(console).submit(
            batch,
            _principal(request),
            dry_run=dry_run,
            expected_version=_expected_version(request, body),
        )
    except InvalidTopologyError as error:
        return json_response(
            {
                "error": str(error),
                "status": 422,
                "violations": error.violations,
                "event": error.event.to_dict() if error.event else None,
            },
            status=422,
        )
    return json_response(result)


ROUTE_TABLE = (
    ("GET", routes.TOPOLOGY, current),
    ("POST", routes.TOPOLOGY, register),
    ("GET", routes.TOPOLOGY_VERSIONS, versions),
    ("GET", routes.TOPOLOGY_VERSION, version),
    ("GET", routes.TOPOLOGY_RESOLVE, resolve),
    ("GET", routes.TOPOLOGY_REPLACEMENT, replacement),
    ("GET", routes.TOPOLOGY_AUDIT, audit),
)
