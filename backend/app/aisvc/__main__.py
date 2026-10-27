"""`python -m app.aisvc`: serve the ai process on the host/port of AIHIRE_AI_SERVICE_URL (loopback/private only)."""
from urllib.parse import urlparse

import uvicorn

from app.aisvc.service import create_app, default_engines
from app.core.config import get_settings
from app.core.logging import configure_logging


def main() -> None:
    configure_logging()
    s = get_settings()
    url = urlparse(s.ai_service_url)  # Settings already refuses public hosts (SEC-06)
    app = create_app(default_engines(), token=s.ai_token.get_secret_value())
    uvicorn.run(app, host=url.hostname or "127.0.0.1", port=url.port or 8200, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
