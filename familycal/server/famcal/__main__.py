import logging, os
import uvicorn
from .app import create_app

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("FAMCAL_PORT", "8091")))
