import ipaddress

from fastapi import APIRouter, HTTPException, Request

from ..models import ScanRequest

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


@router.get("")
def discovery_status(request: Request) -> dict:
    return request.app.state.discovery.snapshot()


@router.post("/scan")
def start_scan(request: Request, body: ScanRequest | None = None) -> dict:
    body = body or ScanRequest()
    if body.subnet:
        try:
            ipaddress.ip_network(body.subnet, strict=False)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid subnet: {exc}") from None
    discovery = request.app.state.discovery
    started = discovery.start_scan(mdns=body.mdns, subnet=body.subnet,
                                   duration_s=body.duration_s)
    snapshot = discovery.snapshot()
    snapshot["started"] = started
    return snapshot
