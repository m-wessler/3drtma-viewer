from flask import Flask, render_template, request, jsonify
import os
import logging
import json
from datetime import datetime, timedelta
import tempfile
import subprocess
import shutil
import sys
import requests
import re
import numpy as np
import base64
from urllib.parse import urlencode

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


from app_utils import date_to_yyyymmdd, validate_pressure_level

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

# Virtual / convenience data source: variables present in both 3DRTMA and RTMA
DATA_SOURCES['3DRTMA_minus_RTMA'] = '3DRTMA minus RTMA'

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

        # For RTMA (surface) data, do not coerce pressure_level to an int.
        # Let the GRIB inventory determine the appropriate surface-level record.
        if data_source == 'RTMA':
            pressure_level = None
        else:
            # Validate pressure_level if provided for pressure-enabled sources
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
        
        # For RTMA (surface) data, ignore numeric pressure levels and let the
        # GRIB inventory determine the correct surface record. For other data
        # sources, validate/convert the pressure_level to int.
        if data_source == 'RTMA':
            pressure_level = None
        else:
            if pressure_level is not None and pressure_level != '':
                try:
                    pressure_level = int(pressure_level)
                except Exception:
                    return jsonify({'error': 'Invalid pressure_level; must be integer'}), 400

        # Get variable data using lazy generator
        wg = get_weather_generator()

        # Special handling: if the user requested the virtual "3DRTMA_minus_RTMA"
        # dataset, compute a difference between the 3DRTMA best-matched level and RTMA surface.
        if data_source in ('3DRTMA_minus_RTMA', '3DRTMA minus RTMA', '3DRTMA-RTMA'):
            comps = compute_comparable_grids(date_formatted, hour)
            match = next((c for c in comps.get('comparisons', []) if c.get('variable') == variable), None)
            if not match or not match.get('best_match_3d_level'):
                return jsonify({'success': False, 'error': f'No comparable 3DRTMA level found for variable {variable}'}), 400

            best_level = match['best_match_3d_level']

            # Load 3DRTMA var at matched level
            grib3, idx3 = wg.generate_urls(date_formatted, hour, '3DRTMA')
            var3, coords3 = wg.processor.load_single_variable(grib3, idx3, variable, best_level)
            if not var3 or coords3 is None:
                return jsonify({'success': False, 'error': f'Failed to load 3DRTMA variable {variable} at {best_level}mb'}), 500

            # Load RTMA surface var
            gribr, idxr = wg.generate_urls(date_formatted, hour, 'RTMA')
            varr, coordsr = wg.processor.load_single_variable(gribr, idxr, variable, None)
            if not varr or coordsr is None:
                return jsonify({'success': False, 'error': f'Failed to load RTMA variable {variable}'}), 500

            data3 = np.array(var3['data'])
            datar = np.array(varr['data'])

            # nearest-neighbor resample of RTMA to 3D grid if needed
            def resample_to_grid(src_data, src_lat, src_lon, tgt_lat, tgt_lon):
                try:
                    src_lats = np.unique(src_lat[:,0])
                    src_lons = np.unique(src_lon[0,:])
                except Exception:
                    src_lats = np.unique(src_lat.flatten())
                    src_lons = np.unique(src_lon.flatten())

                tgt_shape = tgt_lat.shape
                res = np.full(tgt_shape, np.nan, dtype=float)
                for i in range(tgt_shape[0]):
                    lat_val = tgt_lat[i,0]
                    lat_idx = int(np.argmin(np.abs(src_lats - lat_val)))
                    for j in range(tgt_shape[1]):
                        lon_val = tgt_lon[0,j]
                        lon_idx = int(np.argmin(np.abs(src_lons - lon_val)))
                        try:
                            res[i,j] = src_data[lat_idx, lon_idx]
                        except Exception:
                            res[i,j] = np.nan
                return res

            if datar.shape != data3.shape:
                datar_resampled = resample_to_grid(datar, coordsr['lat_grid'], coordsr['lon_grid'], coords3['lat_grid'], coords3['lon_grid'])
            else:
                datar_resampled = datar

            diff = data3 - datar_resampled
            vabs = np.nanmax(np.abs(diff)) if np.isfinite(np.nanmax(np.abs(diff))) else 0.0
            levels = np.linspace(-vabs, vabs, wg.config.CONTOUR_LEVELS if wg.config.CONTOUR_LEVELS>1 else 11)

            img_data = wg.renderer.create_contour_overlay(coords3['lon_grid'], coords3['lat_grid'], diff, levels=levels, cmap='RdBu_r')
            # Persist overlay as PNG to static maps and return a compact URL to avoid huge JSON payloads
            static_dir = os.path.join(app.root_path, 'static', 'maps')
            os.makedirs(static_dir, exist_ok=True)
            png_filename = f'diff_{variable}_{date_formatted}_{hour:02d}Z.png'
            png_path = os.path.join(static_dir, png_filename)
            try:
                with open(png_path, 'wb') as fh:
                    fh.write(base64.b64decode(img_data))
                image_url = f'/static/maps/{png_filename}'
            except Exception as e:
                logger.error(f'Failed to write overlay PNG: {e}', exc_info=True)
                # Fallback to inline base64 if file write fails
                image_url = None

            bounds = [[float(coords3['lat_grid'].min()), float(coords3['lon_grid'].min())], [float(coords3['lat_grid'].max()), float(coords3['lon_grid'].max())]]
            resp = {'success': True, 'bounds': bounds}
            if image_url:
                resp['image_url'] = image_url
            else:
                resp['image_data'] = img_data
            return jsonify(resp)

        # Fallback: use existing generator JSON helper
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


@app.route('/sample_point', methods=['POST'])
def sample_point():
    """Sample the data value at a given lat/lon for the selected variable and data source.

    Expects JSON: { lat, lon, date, hour, variable, data_source, pressure_level }
    """
    try:
        data = request.get_json()
        logger.debug(f'/sample_point payload: {data}')

        # Diagnostic logging (best-effort)
        try:
            logs_dir = os.path.join(app.root_path, 'logs')
            os.makedirs(logs_dir, exist_ok=True)
            diag_path = os.path.join(logs_dir, 'sample_point_calls.log')
            with open(diag_path, 'a', encoding='utf8') as df:
                df.write(json.dumps({'time': datetime.utcnow().isoformat(), 'payload': data}) + '\n')
        except Exception:
            # Don't let diagnostic logging break the endpoint
            pass

        lat = float(data.get('lat'))
        lon = float(data.get('lon'))
        date_str = data.get('date')
        hour = int(data.get('hour', 12))
        variable = data.get('variable')
        data_source = data.get('data_source', 'RTMA')
        pressure_level = data.get('pressure_level')

        if not all([date_str, variable]):
            return jsonify({'success': False, 'error': 'date and variable are required'}), 400

        try:
            date_formatted = date_to_yyyymmdd(date_str)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format'}), 400

        wg = get_weather_generator()

        # For virtual dataset, compute diff and sample
        if data_source in ('3DRTMA_minus_RTMA', '3DRTMA minus RTMA', '3DRTMA-RTMA'):
            comps = compute_comparable_grids(date_formatted, hour)
            match = next((c for c in comps.get('comparisons', []) if c.get('variable') == variable), None)
            if not match or not match.get('best_match_3d_level'):
                return jsonify({'success': False, 'error': f'No comparable 3DRTMA level for {variable}'}), 400
            best_level = match['best_match_3d_level']

            grib3, idx3 = wg.generate_urls(date_formatted, hour, '3DRTMA')
            var3, coords3 = wg.processor.load_single_variable(grib3, idx3, variable, best_level)
            if var3 is None or coords3 is None:
                return jsonify({'success': False, 'error': 'Failed to load 3DRTMA data'}), 500

            gribr, idxr = wg.generate_urls(date_formatted, hour, 'RTMA')
            varr, coordsr = wg.processor.load_single_variable(gribr, idxr, variable, None)
            if varr is None or coordsr is None:
                return jsonify({'success': False, 'error': 'Failed to load RTMA data'}), 500

            data3 = np.array(var3['data'])
            datar = np.array(varr['data'])

            # nearest-neighbor resample of RTMA to 3D grid if needed
            def resample_to_grid(src_data, src_lat, src_lon, tgt_lat, tgt_lon):
                try:
                    src_lats = np.unique(src_lat[:,0])
                    src_lons = np.unique(src_lon[0,:])
                except Exception:
                    src_lats = np.unique(src_lat.flatten())
                    src_lons = np.unique(src_lon.flatten())

                tgt_shape = tgt_lat.shape
                res = np.full(tgt_shape, np.nan, dtype=float)
                for ii in range(tgt_shape[0]):
                    lat_val = tgt_lat[ii,0]
                    lat_idx = int(np.argmin(np.abs(src_lats - lat_val)))
                    for jj in range(tgt_shape[1]):
                        lon_val = tgt_lon[0,jj]
                        lon_idx = int(np.argmin(np.abs(src_lons - lon_val)))
                        try:
                            res[ii,jj] = src_data[lat_idx, lon_idx]
                        except Exception:
                            res[ii,jj] = np.nan
                return res

            if datar.shape != data3.shape:
                datar_resampled = resample_to_grid(datar, coordsr['lat_grid'], coordsr['lon_grid'], coords3['lat_grid'], coords3['lon_grid'])
            else:
                datar_resampled = datar

            diff = data3 - datar_resampled

            # Find nearest grid point in coords3 to requested lat/lon
            lat_grid = coords3['lat_grid']
            lon_grid = coords3['lon_grid']
            flat_lats = lat_grid[:,0]
            flat_lons = lon_grid[0,:]
            i = int(np.argmin(np.abs(flat_lats - lat)))
            j = int(np.argmin(np.abs(flat_lons - lon)))
            sampled = float(diff[i, j]) if np.isfinite(diff[i, j]) else None

            # Determine units: prefer the loaded variable's info; fall back to generator config VARIABLE_INFO
            units = ''
            try:
                if isinstance(var3, dict):
                    units = var3.get('info', {}).get('units', '') or ''
            except Exception:
                units = ''
            if not units and hasattr(wg, 'config'):
                units = getattr(wg.config, 'VARIABLE_INFO', {}).get(variable, {}).get('units', '')

            # Grid cell lat/lon for the sampled i,j
            try:
                grid_lat = float(coords3['lat_grid'][i, j])
                grid_lon = float(coords3['lon_grid'][i, j])
            except Exception:
                # fall back to row/col selection
                grid_lat = float(coords3['lat_grid'][i, 0])
                grid_lon = float(coords3['lon_grid'][0, j])

            # Reverse geocode (best-effort, short timeout)
            location_name = ''
            try:
                nom_url = 'https://nominatim.openstreetmap.org/reverse'
                params = {'format': 'jsonv2', 'lat': grid_lat, 'lon': grid_lon}
                headers = {'User-Agent': '3drtma-viewer/1.0 (github:m-wessler)'}
                r = requests.get(nom_url, params=params, headers=headers, timeout=5)
                if r.status_code == 200:
                    jr = r.json()
                    location_name = jr.get('display_name', '')
            except Exception:
                location_name = ''

            return jsonify({
                'success': True,
                'value': sampled,
                'units': units or '',
                'grid_i': int(i),
                'grid_j': int(j),
                'requested_lat': lat,
                'requested_lon': lon,
                'grid_lat': grid_lat,
                'grid_lon': grid_lon,
                'location_name': location_name
            })

        # Non-virtual: load single variable and sample
        if data_source == 'RTMA':
            pressure_level = None
        else:
            if pressure_level is not None and pressure_level != '':
                try:
                    pressure_level = int(pressure_level)
                except Exception:
                    return jsonify({'success': False, 'error': 'Invalid pressure_level'}), 400

        grib, idx = wg.generate_urls(date_formatted, hour, data_source)
        var, coords = wg.processor.load_single_variable(grib, idx, variable, pressure_level)
        if var is None or coords is None:
            return jsonify({'success': False, 'error': 'Failed to load variable data'}), 500

        data_arr = np.array(var['data'])
        lat_grid = coords['lat_grid']
        lon_grid = coords['lon_grid']
        flat_lats = lat_grid[:,0]
        flat_lons = lon_grid[0,:]
        i = int(np.argmin(np.abs(flat_lats - lat)))
        j = int(np.argmin(np.abs(flat_lons - lon)))
        sampled = float(data_arr[i, j]) if np.isfinite(data_arr[i, j]) else None

        # Determine units
        units = ''
        try:
            if isinstance(var, dict):
                units = var.get('units') or var.get('info', {}).get('units') or ''
        except Exception:
            units = ''
        if not units and hasattr(wg, 'config'):
            units = getattr(wg.config, 'VARIABLE_INFO', {}).get(variable, {}).get('units', '')

        # Compute grid cell coordinates and optional reverse geocode (best-effort)
        try:
            grid_lat = float(lat_grid[i])
            grid_lon = float(lon_grid[j])
        except Exception:
            try:
                grid_lat = float(lat_grid[i, 0])
                grid_lon = float(lon_grid[0, j])
            except Exception:
                grid_lat = None
                grid_lon = None

        location_name = ''
        if grid_lat is not None and grid_lon is not None:
            try:
                nom_url = 'https://nominatim.openstreetmap.org/reverse'
                params = {'format': 'jsonv2', 'lat': grid_lat, 'lon': grid_lon}
                headers = {'User-Agent': '3drtma-viewer/1.0 (github:m-wessler)'}
                r = requests.get(nom_url, params=params, headers=headers, timeout=5)
                if r.status_code == 200:
                    jr = r.json()
                    location_name = jr.get('display_name', '')
            except Exception:
                location_name = ''

        return jsonify({
            'success': True,
            'value': sampled,
            'units': units or '',
            'grid_i': int(i),
            'grid_j': int(j),
            'requested_lat': lat,
            'requested_lon': lon,
            'grid_lat': grid_lat,
            'grid_lon': grid_lon,
            'location_name': location_name
        })

    except Exception as e:
        logger.error(f'Error sampling point: {e}', exc_info=True)
        return jsonify({'success': False, 'error': f'Error sampling point: {str(e)}'}), 500


def _parse_grib_index(idx_url: str):
    """Fetch and parse a GRIB .idx file into a mapping of variable -> list of level strings.

    Returns: dict { variable_name: [level_str,...] }
    """
    try:
        resp = requests.get(idx_url, timeout=20)
        resp.raise_for_status()
        lines = resp.text.strip().split('\n')
        mapping = {}
        for line in lines:
            parts = line.split(':')
            if len(parts) >= 6:
                var = parts[3]
                level = parts[4]
                mapping.setdefault(var, []).append(level)
        return mapping
    except Exception as e:
        logger.warning(f'Unable to fetch/parse idx {idx_url}: {e}')
        return {}


def compute_comparable_grids(date_formatted: str, hour: int):
    """Compute comparable grids between RTMA and 3DRTMA for a date/hour.

    Returns a dict that mirrors the /get_comparable_grids JSON response and
    also writes the result to logs/comparable_grids_{date}_{hour}.json
    """
    wg = get_weather_generator()

    # Build idx URLs for both sources
    rtma_grib, rtma_idx = wg.generate_urls(date_formatted, hour, 'RTMA')
    three_grib, three_idx = wg.generate_urls(date_formatted, hour, '3DRTMA')

    rtma_map = _parse_grib_index(rtma_idx)
    three_map = _parse_grib_index(three_idx)

    # variables union
    vars_union = set(list(rtma_map.keys()) + list(three_map.keys()))

    comparisons = []

    # helper to parse pressure ints from level strings
    def extract_pressure_ints(levels):
        ints = []
        for lv in levels:
            if not lv:
                continue
            m = re.search(r"(\d{2,4})\s*mb", (lv or '').lower())
            if m:
                try:
                    ints.append(int(m.group(1)))
                except Exception:
                    continue
            # catch plain numbers
            else:
                m2 = re.search(r"^(\d{2,4})$", (lv or '').strip())
                if m2:
                    ints.append(int(m2.group(1)))
        return sorted(list(set(ints)))

    for var in sorted(vars_union):
        rtma_levels = rtma_map.get(var, [])
        three_levels_raw = three_map.get(var, [])

        # detect if RTMA has 2 m or surface
        rtma_has_2m = any('2 m' in (l or '').lower() or '2m' in (l or '').lower() for l in rtma_levels)
        rtma_has_surface = any('surface' in (l or '').lower() or 'sfc' in (l or '').lower() for l in rtma_levels)

        three_levels = extract_pressure_ints(three_levels_raw)

        # Heuristic best match: if RTMA has 2m/surface, prefer near-surface pressure (~1000,925,850)
        best_match = None
        best_diff = None
        if three_levels:
            if rtma_has_2m or rtma_has_surface:
                targets = [1000, 925, 850, 700]
                # pick the closest available to any of the targets
                for candidate in three_levels:
                    for t in targets:
                        d = abs(candidate - t)
                        if best_diff is None or d < best_diff:
                            best_diff = d
                            best_match = candidate
            else:
                # no 2m info -> pick median/nearest to 500 mb as a generic middle level
                median = three_levels[len(three_levels)//2]
                best_match = median

        comparisons.append({
            'variable': var,
            'rtma_levels': rtma_levels,
            'rtma_has_2m': rtma_has_2m,
            'rtma_has_surface': rtma_has_surface,
            'three_d_levels': three_levels,
            'best_match_3d_level': best_match,
            'rtma_idx_url': rtma_idx,
            'three_idx_url': three_idx
        })

    result = {'success': True, 'comparisons': comparisons}

    # Also write the result to a log file for offline inspection
    try:
        logs_dir = os.path.join(app.root_path, 'logs')
        os.makedirs(logs_dir, exist_ok=True)
        fname = f'comparable_grids_{date_formatted}_{hour:02d}.json'
        out_path = os.path.join(logs_dir, fname)
        with open(out_path, 'w', encoding='utf8') as f:
            json.dump({'date': date_formatted, 'hour': hour, 'comparisons': comparisons}, f, indent=2)
        logger.info(f'Wrote comparable grids to {out_path}')
    except Exception as e:
        logger.warning(f'Failed to write comparable grids log: {e}')

    return result


@app.route('/get_comparable_grids', methods=['POST'])
def get_comparable_grids():
    """Return a list of comparable grids between RTMA (surface) and 3DRTMA (pressure levels).

    Request JSON: { date: 'YYYY-MM-DD' | 'YYYYMMDD', hour: int }
    Response: { success: True, comparisons: [ { variable, rtma_levels, rtma_has_2m, three_d_levels, best_match_3d_level, idx_urls } ] }
    """
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid request payload'}), 400
        date_str = data.get('date')
        hour = int(data.get('hour', 12))

        if not date_str:
            return jsonify({'error': 'Date is required'}), 400

        try:
            date_formatted = date_to_yyyymmdd(date_str)
        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400

        result = compute_comparable_grids(date_formatted, hour)
        return jsonify(result)

    except Exception as e:
        logger.error(f'Error computing comparable grids: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500

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

        # Support a virtual data source which is the intersection of 3DRTMA and RTMA
        if data_source in ('3DRTMA minus RTMA', '3DRTMA_minus_RTMA', '3DRTMA-RTMA'):
            vars_3d = set(wg.get_filtered_variables(date_formatted, hour, '3DRTMA'))
            vars_rtma = set(wg.get_filtered_variables(date_formatted, hour, 'RTMA'))
            # only keep variables that are in our AVAILABLE_VARIABLES map as well
            variables = sorted(list(vars_3d.intersection(vars_rtma).intersection(set(AVAILABLE_VARIABLES.keys()))))
        else:
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
