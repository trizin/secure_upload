"""Container health check.

Host enforcement means a request to "localhost" is correctly refused with a
404, which would read as a dead container. Send the hostname the app actually
serves.
"""

import os
import sys
import urllib.request

host = os.environ.get("SUPLOAD_APP_HOST", "supload.org")
request = urllib.request.Request("http://127.0.0.1:8000/healthz", headers={"Host": host})

try:
    with urllib.request.urlopen(request, timeout=4) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
    print(exc, file=sys.stderr)
    sys.exit(1)
