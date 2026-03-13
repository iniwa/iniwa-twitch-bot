from flask import Flask
from routes import register_blueprints
from routes.filters import register_filters
from services.workers import start_workers

start_workers()

app = Flask(__name__)
register_filters(app)
register_blueprints(app)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8501, debug=False)
