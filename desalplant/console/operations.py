"""Operations that change the state of the line."""

from ..topology.record import Equipment, MeasurementPoint
from .http import Request, Response, json_response


def start(console, request: Request) -> Response:
    """Bring the line up."""
    request.body()
    event = console.plant.start()
    return json_response({"status": "started", "event": event.to_dict()})


def shutdown(console, request: Request) -> Response:
    """Stop the line."""
    request.body()
    event = console.plant.shutdown()
    return json_response({"status": "stopped", "event": event.to_dict()})


def switch(console, request: Request) -> Response:
    """Move production to the standby tank."""
    request.body()
    event = console.plant.switch_tank()
    return json_response({"status": "switched", "event": event.to_dict()})


def maintain(console, request: Request) -> Response:
    """Run the maintenance routine."""
    request.body()
    event = console.plant.maintain()
    return json_response({"status": "maintained", "event": event.to_dict()})


def _parse_topology_body(body: dict):
    """Decode equipment and point rows from a request body."""
    equipment = [Equipment.from_dict(row) for row in body.get("equipment", [])]
    points = [MeasurementPoint.from_dict(row) for row in body.get("points", [])]
    return equipment, points


def topology_validate(console, request: Request) -> Response:
    """Validate a target topology without committing it (dry run)."""
    equipment, points = _parse_topology_body(request.body())
    return json_response(console.plant.topology.validate(equipment, points))


def topology_apply(console, request: Request) -> Response:
    """Validate and commit a topology batch as one atomic change."""
    body = request.body()
    actor = str(body.get("actor", "unknown"))
    equipment, points = _parse_topology_body(body)
    return json_response(console.plant.topology.apply(equipment, points, actor))


def topology_rename(console, request: Request) -> Response:
    """Rename a tag as a versioned migration (requires the rename permission)."""
    body = request.body()
    return json_response(
        console.plant.topology.rename(
            old_id=str(body["old_id"]),
            new_id=str(body["new_id"]),
            actor=str(body.get("actor", "unknown")),
            role=str(body.get("role", "viewer")),
            reason=str(body.get("reason", "")),
        )
    )
