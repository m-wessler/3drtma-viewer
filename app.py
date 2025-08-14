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

# Available data sources
DATA_SOURCES = {
    'RTMA': 'RTMA 2.5km Surface',
    '3DRTMA': '3D-RTMA Pressure Levels'
}

@app.route('/')
def index():
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    
    return render_template('index.html', 
                         variables=AVAILABLE_VARIABLES,
                         data_sources=DATA_SOURCES,
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
        data_source = data.get('data_source', 'RTMA')  # Default to RTMA
        pressure_level = data.get('pressure_level')  # Optional pressure level for 3DRTMA
        
        if not date_str:
            return jsonify({'error': 'Date is required'}), 400
        
        # Parse date
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            date_formatted = date_obj.strftime('%Y%m%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        # Create output file in static directory
        output_filename = f'weather_map_{data_source}_{date_formatted}_{hour:02d}Z.html'
        static_dir = os.path.join(app.root_path, 'static', 'maps')
        os.makedirs(static_dir, exist_ok=True)
        output_path = os.path.join(static_dir, output_filename)
        
        logger.info(f'Generating weather map for {date_formatted} {hour:02d}Z using {data_source}')
        
        # Use the new single variable approach with data source
        success = weather_generator.create_single_variable_weather_map(
            date_formatted, hour, output_path, variable, data_source, pressure_level
        )
        
        if success:
            return jsonify({
                'success': True,
                'map_url': f'/static/maps/{output_filename}',
                'date': date_str,
                'hour': hour,
                'variable': variable,
                'data_source': data_source,
                'message': 'Weather map generated successfully!'
            })
        else:
            error_msg = 'Failed to generate weather map'
            return jsonify({'error': error_msg}), 500
            
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
        data_source = data.get('data_source', 'RTMA')  # Default to RTMA
        pressure_level = data.get('pressure_level')  # Optional pressure level for 3DRTMA
        
        logger.info(f'Received AJAX request: date={date_str}, hour={hour}, variable={variable}, source={data_source}')
        
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
        
        logger.info(f'Getting variable data for {variable} at {date_formatted} {hour:02d}Z using {data_source}')
        
        # Get variable data
        result = weather_generator.get_variable_data_json(date_formatted, hour, variable, data_source, pressure_level)
        
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

@app.route('/get_pressure_levels', methods=['POST'])
def get_pressure_levels():
    """Get available pressure levels for 3DRTMA data."""
    try:
        data = request.get_json()
        date_str = data.get('date')
        hour = int(data.get('hour', 12))
        data_source = data.get('data_source', 'RTMA')
        
        if not date_str:
            return jsonify({'error': 'Date is required'}), 400
        
        # Parse date
        try:
            if len(date_str) == 8 and date_str.isdigit():
                date_formatted = date_str
            else:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                date_formatted = date_obj.strftime('%Y%m%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400
        
        # Get pressure levels
        pressure_levels = weather_generator.get_available_pressure_levels(date_formatted, hour, data_source)
        
        return jsonify({
            'success': True,
            'pressure_levels': pressure_levels,
            'common_levels': weather_generator.config.COMMON_PRESSURE_LEVELS
        })
        
    except Exception as e:
        logger.error(f'Error getting pressure levels: {str(e)}', exc_info=True)
        return jsonify({'error': f'Error getting pressure levels: {str(e)}'}), 500

@app.route('/get_filtered_variables', methods=['POST'])
def get_filtered_variables():
    """Get available variables filtered for the selected data source."""
    try:
        data = request.get_json()
        date_str = data.get('date')
        hour = int(data.get('hour', 12))
        data_source = data.get('data_source', 'RTMA')
        
        if not date_str:
            return jsonify({'error': 'Date is required'}), 400
        
        # Parse date
        try:
            if len(date_str) == 8 and date_str.isdigit():
                date_formatted = date_str
            else:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                date_formatted = date_obj.strftime('%Y%m%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400
        
        # Get filtered variables
        variables = weather_generator.get_filtered_variables(date_formatted, hour, data_source)
        
        # Create variables with descriptions
        variables_with_desc = {}
        for var in variables:
            if var in weather_generator.config.VARIABLE_INFO:
                var_info = weather_generator.config.VARIABLE_INFO[var]
                variables_with_desc[var] = f"{var_info['name']} ({var_info['units']})"
            elif var in AVAILABLE_VARIABLES:
                variables_with_desc[var] = AVAILABLE_VARIABLES[var]
            else:
                variables_with_desc[var] = var
        
        return jsonify({
            'success': True,
            'variables': variables_with_desc
        })
        
    except Exception as e:
        logger.error(f'Error getting filtered variables: {str(e)}', exc_info=True)
        return jsonify({'error': f'Error getting filtered variables: {str(e)}'}), 500

@app.route('/get_variables_for_pressure_level', methods=['POST'])
def get_variables_for_pressure_level():
    """Get available variables for a specific pressure level in 3DRTMA data."""
    try:
        data = request.get_json()
        date_str = data.get('date')
        hour = int(data.get('hour', 12))
        data_source = data.get('data_source', 'RTMA')
        pressure_level = data.get('pressure_level')
        
        if not all([date_str, pressure_level is not None]):
            return jsonify({'error': 'Date and pressure level are required'}), 400
        
        # Parse date
        try:
            if len(date_str) == 8 and date_str.isdigit():
                date_formatted = date_str
            else:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                date_formatted = date_obj.strftime('%Y%m%d')
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400
        
        # Get variables for specific pressure level
        variables = weather_generator.get_variables_for_pressure_level(date_formatted, hour, data_source, int(pressure_level))
        
        # Create variables with descriptions
        variables_with_desc = {}
        for var in variables:
            if var in weather_generator.config.VARIABLE_INFO:
                var_info = weather_generator.config.VARIABLE_INFO[var]
                variables_with_desc[var] = f"{var_info['name']} ({var_info['units']})"
            elif var in AVAILABLE_VARIABLES:
                variables_with_desc[var] = AVAILABLE_VARIABLES[var]
            else:
                variables_with_desc[var] = var
        
        return jsonify({
            'success': True,
            'variables': variables_with_desc
        })
        
    except Exception as e:
        logger.error(f'Error getting variables for pressure level: {str(e)}', exc_info=True)
        return jsonify({'error': f'Error getting variables for pressure level: {str(e)}'}), 500

if __name__ == '__main__':
    # Create necessary directories
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/maps', exist_ok=True)
    
    print('Starting Weather Map Web Application...')
    print('Open your browser to: http://localhost:5000')
    
    app.run(debug=True, host='0.0.0.0', port=5000)
