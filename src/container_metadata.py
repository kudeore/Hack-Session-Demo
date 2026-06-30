from __future__ import annotations

import os
import socket
from typing import Dict


def get_container_metadata() -> Dict[str, str | None]:
    """Return lightweight deployment metadata for audit/debug evidence."""

    return {
        "container_id": os.getenv("CONTAINER_ID") or os.getenv("HOSTNAME") or socket.gethostname(),
        "pod_name": os.getenv("POD_NAME"),
        "pod_namespace": os.getenv("POD_NAMESPACE"),
        "node_name": os.getenv("NODE_NAME"),
        "service_name": os.getenv("SERVICE_NAME", "governed-refund-agent"),
        "deployment_environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
    }
