from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Minimal placeholder worker for POC. Replace internals with real GRIB processing.

@app.route('/render', methods=['POST'])
def render():
    payload = request.json or {}
    variable = payload.get('variable', 'TMP')
    # Return a placeholder image URL and world bounds
    return jsonify({
        'image_url': 'https://via.placeholder.com/1024x768.png?text=3DRTMA+' + variable,
        'bounds': [[-60, -140], [70, -30]],
        'source': 'python-worker-placeholder',
        'generated_at': datetime.utcnow().isoformat() + 'Z'
    })

@app.route('/sample', methods=['POST'])
def sample():
    payload = request.json or {}
    lat = payload.get('lat')
    lon = payload.get('lon')
    # Return a fake sampled value for demonstration
    value = round(280.0 + (lat or 0) * 0.01 + (lon or 0) * 0.005, 2)
    return jsonify({
        'value': value,
        'units': 'K',
        'lat': lat,
        'lon': lon,
        'location': 'POC location'
    })

if __name__ == '__main__':
    app.run(port=5001, debug=True)
