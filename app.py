import os
import logging
from flask import Flask
from routes import register_blueprints
from routes.filters import register_filters
from services.workers import start_workers

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
register_filters(app)
register_blueprints(app)

try:
    start_workers()
except Exception as e:
    logger.error(f'Worker startup failed: {e}')
    raise

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8501))
    app.run(host='0.0.0.0', port=port, debug=False)
