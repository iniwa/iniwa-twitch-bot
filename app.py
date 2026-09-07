import os
import atexit
import logging
from routes.application import create_app
from services.workers import start_workers, shutdown_workers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = create_app()

try:
    start_workers()
    atexit.register(shutdown_workers)
except Exception as e:
    logger.error(f'Worker startup failed: {e}')
    raise

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8501))
    app.run(host='0.0.0.0', port=port, debug=False)
