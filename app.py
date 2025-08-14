from flask import Flask, render_template, request, jsonify
import os
import logging
from datetime import datetime, timedelta
import tempfile
import subprocess
import shutil
import sys
import requests

# Lazy import of WeatherMapGenerator to avoid heavy imports at module import time

app = Flask(__name__)

# Use an environment-provided secret in production; fallback to a random key for dev.
secret = os.environ.get('FLASK_SECRET')
if secret:
    app.secret_key = secret
else:
    # non-persistent fallback for local development
    app.secret_key = os.urandom(24)

# Weather generator will be created lazily on first use to avoid heavy startup costs / import errors
weather_generator = None

def get_weather_generator():
    """Lazily import and instantiate WeatherMapGenerator from `test.py`.

    This prevents the Flask app from failing to import when system deps for cfgrib / eccodes
    are missing during quick code checks.
    """
    global weather_generator
    if weather_generator is None:
        try:
            # Import the class only when needed
            sys.path.insert(0, os.path.dirname(__file__))
            from test import WeatherMapGenerator
            weather_generator = WeatherMapGenerator()
        except Exception as e:
            logging.getLogger(__name__).error(f'Failed to create WeatherMapGenerator: {e}', exc_info=True)
            raise
    return weather_generator


def date_to_yyyymmdd(date_str: str) -> str:
    """Normalize date string to YYYYMMDD. Accepts YYYY-MM-DD or YYYYMMDD.

    Raises ValueError on invalid input.
    """
    if not date_str:
        raise ValueError('Empty date string')
    if isinstance(date_str, str) and len(date_str) == 8 and date_str.isdigit():
        return date_str
    # support YYYY-MM-DD
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%Y%m%d')
    except Exception:
        raise ValueError('Invalid date format')


def validate_pressure_level(value):
    """Validate and convert pressure level input to int.

    Accepts int or numeric string. Raises ValueError for missing/invalid values.
    """
    if value is None or (isinstance(value, str) and value.strip() == ''):
        raise ValueError('pressure_level is required')
    try:
        return int(value)
    except Exception:
        raise ValueError('Invalid pressure_level; must be integer')

# Create weather map generator instance (created lazily by get_weather_generator)

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
        logger.debug(f'/generate_map payload: {data}')
        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid request payload, expected JSON object', 'received': str(data)}), 400
        date_str = data.get('date')
        hour = int(data.get('hour', 12))
        variable = data.get('variable', 'TMP')  # Default to temperature
        data_source = data.get('data_source', 'RTMA')  # Default to RTMA
        pressure_level = data.get('pressure_level')  # Optional pressure level for 3DRTMA
        
        if not date_str:
            return jsonify({'error': 'Date is required', 'received': data}), 400
        
        # Normalize date to YYYYMMDD (accept YYYY-MM-DD or YYYYMMDD)
        try:
            date_formatted = date_to_yyyymmdd(date_str)
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD or YYYYMMDD', 'received': date_str}), 400
        
        # Create output file in static directory
        output_filename = f'weather_map_{data_source}_{date_formatted}_{hour:02d}Z.html'
        static_dir = os.path.join(app.root_path, 'static', 'maps')
        os.makedirs(static_dir, exist_ok=True)
        output_path = os.path.join(static_dir, output_filename)
        
        logger.info(f'Generating weather map for {date_formatted} {hour:02d}Z using {data_source}')

        # Validate pressure_level if provided
        if pressure_level is not None and pressure_level != '':
            try:
                pressure_level = validate_pressure_level(pressure_level)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400

        # Use the new single variable approach with data source (lazy generator)
        wg = get_weather_generator()
        success = wg.create_single_variable_weather_map(
            date_formatted, hour, output_path, variable, data_source, pressure_level
        )
        
        if success:
            return jsonify({
                'success': True,
                'map_url': f'/static/maps/{output_filename}',
                'date': date_str,
                'date_formatted': date_formatted,
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
        logger.debug(f'/get_variable_data payload: {data}')
        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid request payload, expected JSON object', 'received': str(data)}), 400
        date_str = data.get('date')
        hour = int(data.get('hour', 12))
        variable = data.get('variable')
        data_source = data.get('data_source', 'RTMA')  # Default to RTMA
        pressure_level = data.get('pressure_level')  # Optional pressure level for 3DRTMA
        
        logger.info(f'Received AJAX request: date={date_str}, hour={hour}, variable={variable}, source={data_source}')
        
        if not all([date_str, variable]):
            return jsonify({'error': 'Date and variable are required'}), 400
        
        # Normalize date to YYYYMMDD
        try:
            date_formatted = date_to_yyyymmdd(date_str)
        except ValueError as e:
            logger.error(f'Invalid date format: {date_str}, error: {e}')
            return jsonify({'error': f'Invalid date format: {date_str}. Use YYYY-MM-DD or YYYYMMDD', 'received': date_str}), 400
        
        logger.info(f'Getting variable data for {variable} at {date_formatted} {hour:02d}Z using {data_source}')
        
        # Validate pressure_level if provided
        if pressure_level is not None and pressure_level != '':
            try:
                pressure_level = int(pressure_level)
            except Exception:
                return jsonify({'error': 'Invalid pressure_level; must be integer'}), 400

        # Get variable data using lazy generator
        wg = get_weather_generator()
        result = wg.get_variable_data_json(date_formatted, hour, variable, data_source, pressure_level)
        
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
        data_source = data.get('data_source', 'RTMA')
        hour = int(data.get('hour', 12))
        
        if not date_str:
            return jsonify({'error': 'Date is required'}), 400
        
        # Normalize date
        try:
            date_formatted = date_to_yyyymmdd(date_str)
        except ValueError:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD or YYYYMMDD'}), 400
        
        # Build URL to check using the selected data source pattern from the generator config
        try:
            wg = get_weather_generator()
            cfg = wg.config
            # Ensure requested data_source exists in config, otherwise fallback to RTMA
            if data_source not in cfg.DATA_SOURCES:
                data_source_key = cfg.DEFAULT_DATA_SOURCE
            else:
                data_source_key = data_source

            ds_info = cfg.DATA_SOURCES[data_source_key]
            pattern = ds_info.get('idx_pattern')
            # Some patterns expect base_url and numeric hour/date placeholders
            index_url = pattern.format(base_url=ds_info.get('base_url'), date=date_formatted, hour=hour)
        except Exception:
            # Fallback to original RTMA index URL
            base_url = 'https://noaa-rtma-pds.s3.amazonaws.com'
            index_url = f'{base_url}/rtma2p5.{date_formatted}/rtma2p5.t{hour:02d}z.2dvaranl_ndfd.grb2_wexp.idx'

        # Try to fetch the index file to check availability
        try:
            response = requests.head(index_url, timeout=10)
            checked_url = index_url
            if response.status_code == 200:
                return jsonify({
                    'available': True,
                    'date': date_str,
                    'hour': hour,
                    'checked_url': checked_url,
                    'message': f'Data available for {date_str} {hour:02d}Z'
                })
            else:
                return jsonify({
                    'available': False,
                    'checked_url': checked_url,
                    'error': f'Data not available for {date_str} {hour:02d}Z (HTTP {response.status_code})'
                })
        except requests.RequestException as e:
            return jsonify({
                'available': False,
                'checked_url': index_url,
                'error': f'Cannot check data availability: {str(e)}'
            })
            
    except Exception as e:
        logger.error(f'Error checking data availability: {str(e)}')
        return jsonify({'error': f'Error checking availability: {str(e)}'}), 500

@app.route('/debug_info', methods=['GET'])
def debug_info():
    """Debug endpoint to check system status."""
    try:
        # Provide safe debug info; only call generator methods if generator can be created
        wg_created = False
        sample_urls = None
        try:
            wg = get_weather_generator()
            wg_created = True
            if hasattr(wg, 'generate_urls'):
                try:
                    sample_urls = wg.generate_urls('20250801', 12)
                except Exception:
                    sample_urls = None
        except Exception:
            wg_created = False

        info = {
            'weather_generator_created': wg_created,
            'current_time': datetime.now().isoformat(),
            'sample_urls': sample_urls,
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
        
        # Normalize date
        try:
            date_formatted = date_to_yyyymmdd(date_str)
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400

        # Get pressure levels using lazy generator
        wg = get_weather_generator()
        pressure_levels = wg.get_available_pressure_levels(date_formatted, hour, data_source)
        
        return jsonify({
            'success': True,
            'pressure_levels': pressure_levels,
            'common_levels': wg.config.COMMON_PRESSURE_LEVELS
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
        
        # Normalize date
        try:
            date_formatted = date_to_yyyymmdd(date_str)
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400

        # Get filtered variables using lazy generator
        wg = get_weather_generator()
        variables = wg.get_filtered_variables(date_formatted, hour, data_source)
        
        # Create variables with descriptions
        variables_with_desc = {}
        for var in variables:
            if var in wg.config.VARIABLE_INFO:
                var_info = wg.config.VARIABLE_INFO[var]
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
        
        # Normalize date
        try:
            date_formatted = date_to_yyyymmdd(date_str)
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400

        # Validate pressure level
        try:
            pressure_level_int = int(pressure_level)
        except Exception:
            return jsonify({'error': 'Invalid pressure level'}), 400

        # Get variables for specific pressure level using lazy generator
        wg = get_weather_generator()
        variables = wg.get_variables_for_pressure_level(date_formatted, hour, data_source, pressure_level_int)
        
        # Create variables with descriptions
        variables_with_desc = {}
        for var in variables:
            if var in wg.config.VARIABLE_INFO:
                var_info = wg.config.VARIABLE_INFO[var]
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
