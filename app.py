from flask import Flask, render_template, request, jsonify
import os
import logging
from datetime import datetime, timedelta
import tempfile
import subprocess
import shutil
import sys

# Add the current directory to sys.path to import test.py
sys.path.insert(0, os.path.dirname(__file__))
from test import WeatherMapGenerator

app = Flask(__name__)
app.secret_key = 'weather_map_secret_key'

# Create weather map generator instance
weather_generator = WeatherMapGenerator()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Available variables with descriptions
AVAILABLE_VARIABLES = {
    'TMP': 'Temperature (F)',
    'DPT': 'Dew Point (F)', 
    'PRES': 'Pressure (hPa)',
    'TCDC': 'Total Cloud Cover (%)',
    'VIS': 'Visibility (km)',
    'orog': 'Terrain Elevation',
    '2sh': 'Specific Humidity',
    'ceil': 'Cloud Ceiling Height'
}

# Path to the weather script
WEATHER_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), 'test.py')

@app.route('/')
def index():
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    
    return render_template('index.html', 
                         variables=AVAILABLE_VARIABLES,
                         default_date=yesterday.strftime('%Y-%m-%d'),
                         today=today.strftime('%Y-%m-%d'))

@app.route('/generate_map', methods=['POST'])
def generate_map():
    try:
        # Get form data
        data = request.get_json()
        date_str = data.get('date')
        hour = int(data.get('hour', 12))
        variable = data.get('variable', 'TMP')  # Default to temperature
        
        if not date_str:
            return jsonify({'error': 'Date is required'}), 400
        
        # Parse date
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            date_formatted = date_obj.strftime('%Y%m%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        # Create output file in static directory
        output_filename = f'weather_map_{date_formatted}_{hour:02d}Z.html'
        static_dir = os.path.join(app.root_path, 'static', 'maps')
        os.makedirs(static_dir, exist_ok=True)
        output_path = os.path.join(static_dir, output_filename)
        
        logger.info(f'Generating weather map for {date_formatted} {hour:02d}Z')
        
        # Use the new single variable approach
        success = weather_generator.create_single_variable_weather_map(
            date_formatted, hour, output_path, variable
        )
        
        if success:
            return jsonify({
                'success': True,
                'map_url': f'/static/maps/{output_filename}',
                'date': date_str,
                'hour': hour,
                'variable': variable,
                'message': 'Weather map generated successfully!'
            })
        else:
            return jsonify({'error': 'Failed to generate weather map'}), 500
            
    except Exception as e:
        logger.error(f'Error generating map: {str(e)}')
        return jsonify({'error': f'Error generating map: {str(e)}'}), 500

@app.route('/get_variable_data', methods=['POST'])
def get_variable_data():
    """AJAX endpoint to get data for a specific variable."""
    try:
        data = request.get_json()
        date_str = data.get('date')
        hour = int(data.get('hour', 12))
        variable = data.get('variable')
        
        logger.info(f'Received AJAX request: date={date_str}, hour={hour}, variable={variable}')
        
        if not all([date_str, variable]):
            return jsonify({'error': 'Date and variable are required'}), 400
        
        # Parse date - handle both YYYY-MM-DD and YYYYMMDD formats
        date_formatted = None
        try:
            # Try YYYYMMDD format first
            if len(date_str) == 8 and date_str.isdigit():
                date_formatted = date_str
                logger.info(f'Date already in YYYYMMDD format: {date_formatted}')
            else:
                # Try YYYY-MM-DD format
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                date_formatted = date_obj.strftime('%Y%m%d')
                logger.info(f'Converted date from {date_str} to {date_formatted}')
        except ValueError as e:
            logger.error(f'Invalid date format: {date_str}, error: {e}')
            return jsonify({'error': f'Invalid date format: {date_str}. Use YYYY-MM-DD or YYYYMMDD'}), 400
        
        logger.info(f'Getting variable data for {variable} at {date_formatted} {hour:02d}Z')
        
        # Get variable data
        result = weather_generator.get_variable_data_json(date_formatted, hour, variable)
        
        logger.info(f'Variable data result: success={result.get("success", False)}')
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f'Error getting variable data: {str(e)}', exc_info=True)
        return jsonify({'error': f'Error getting variable data: {str(e)}'}), 500

@app.route('/check_data_availability', methods=['POST'])
def check_data_availability():
    try:
        data = request.get_json()
        date_str = data.get('date')
        hour = int(data.get('hour', 12))
        
        if not date_str:
            return jsonify({'error': 'Date is required'}), 400
        
        # Parse date
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        date_formatted = date_obj.strftime('%Y%m%d')
        
        # Build URL to check
        base_url = 'https://noaa-rtma-pds.s3.amazonaws.com'
        index_url = f'{base_url}/rtma2p5.{date_formatted}/rtma2p5.t{hour:02d}z.2dvaranl_ndfd.grb2_wexp.idx'
        
        # Try to fetch the index file to check availability
        import requests
        try:
            response = requests.head(index_url, timeout=10)
            if response.status_code == 200:
                return jsonify({
                    'available': True,
                    'date': date_str,
                    'hour': hour,
                    'message': f'Data available for {date_str} {hour:02d}Z'
                })
            else:
                return jsonify({
                    'available': False,
                    'error': f'Data not available for {date_str} {hour:02d}Z (HTTP {response.status_code})'
                })
        except requests.RequestException as e:
            return jsonify({
                'available': False,
                'error': f'Cannot check data availability: {str(e)}'
            })
            
    except Exception as e:
        logger.error(f'Error checking data availability: {str(e)}')
        return jsonify({'error': f'Error checking availability: {str(e)}'}), 500

@app.route('/debug_info', methods=['GET'])
def debug_info():
    """Debug endpoint to check system status."""
    try:
        info = {
            'weather_generator_created': weather_generator is not None,
            'current_time': datetime.now().isoformat(),
            'sample_urls': weather_generator.generate_urls('20250801', 12) if weather_generator else None,
            'available_variables': list(AVAILABLE_VARIABLES.keys()),
            'flask_debug': app.debug
        }
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Create necessary directories
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/maps', exist_ok=True)
    
    print('Starting Weather Map Web Application...')
    print('Open your browser to: http://localhost:5000')
    
    app.run(debug=True, host='0.0.0.0', port=5000)
