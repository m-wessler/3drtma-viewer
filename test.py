#!/usr/bin/env python3
"""
Weather Map Generator

A Python script to create interactive weather maps from NOAA RTMA GRIB2 data.
Downloads meteorological data and generates HTML maps with multiple variable overlays.

Usage:
    python weather_map_generator.py [options]

Example:
    python weather_map_generator.py --date 20250801 --hour 12 --output weather_map.html
"""

import argparse
import logging
import sys
import os
import json
import tempfile
import io
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import xarray as xr
import numpy as np
import pandas as pd
import requests
import folium
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend to avoid threading issues
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from folium import plugins
from PIL import Image


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WeatherMapConfig:
    """Configuration class for weather map generation."""
    
    # Data source configurations
    DATA_SOURCES = {
        'RTMA': {
            'name': 'RTMA 2.5km Surface',
            'base_url': 'https://noaa-rtma-pds.s3.amazonaws.com',
            'grib_pattern': '{base_url}/rtma2p5.{date}/rtma2p5.t{hour:02d}z.2dvaranl_ndfd.grb2_wexp',
            'idx_pattern': '{base_url}/rtma2p5.{date}/rtma2p5.t{hour:02d}z.2dvaranl_ndfd.grb2_wexp.idx',
            'has_pressure_levels': False
        },
        'RTMA-PRES': {
            'name': 'RTMA 2.5km Pressure Levels', 
            'base_url': 'https://noaa-rtma-pds.s3.amazonaws.com',
            'grib_pattern': '{base_url}/rtma2p5.{date}/rtma2p5.t{hour:02d}z.3dvaranl_ndfd.grb2_wexp',
            'idx_pattern': '{base_url}/rtma2p5.{date}/rtma2p5.t{hour:02d}z.3dvaranl_ndfd.grb2_wexp.idx',
            'has_pressure_levels': True
        },
        '3DRTMA': {
            'name': '3D-RTMA Pressure Levels',
            'base_url': 'https://noaa-nws-3drtma-pds.s3.amazonaws.com',
            'grib_pattern': '{base_url}/3drtma/results/rtma_a/rtma3d_hrrr.v1.0.0/prod/rtma3d.{date}/{hour:02d}/rtma3d.t{hour:02d}z.anl_prslev_ndfd.grib2',
            'idx_pattern': '{base_url}/3drtma/results/rtma_a/rtma3d_hrrr.v1.0.0/prod/rtma3d.{date}/{hour:02d}/rtma3d.t{hour:02d}z.anl_prslev_ndfd.grib2.idx',
            'has_pressure_levels': True
        }
    }
    
    # Default data source
    DEFAULT_DATA_SOURCE = 'RTMA'
    
    # Legacy properties for backward compatibility
    @property
    def BASE_URL(self):
        return self.DATA_SOURCES[self.DEFAULT_DATA_SOURCE]['base_url']
    
    @property 
    def GRIB_PATTERN(self):
        return self.DATA_SOURCES[self.DEFAULT_DATA_SOURCE]['grib_pattern']
    
    @property
    def IDX_PATTERN(self):
        return self.DATA_SOURCES[self.DEFAULT_DATA_SOURCE]['idx_pattern']
    
    # Variable definitions
    VARIABLE_INFO = {
        'GUST': {'name': 'Wind Gust', 'units': 'mph', 'multiplier': 2.237, 'cmap': 'YlOrRd'},
        'UGRD': {'name': 'U-Component Wind', 'units': 'mph', 'multiplier': 2.237, 'cmap': 'RdBu_r'},
        'VGRD': {'name': 'V-Component Wind', 'units': 'mph', 'multiplier': 2.237, 'cmap': 'RdBu_r'},
        'WIND': {'name': 'Wind Speed', 'units': 'mph', 'multiplier': 2.237, 'cmap': 'plasma'},
        'TMP': {'name': 'Temperature', 'units': '°F', 'multiplier': 1.8, 'offset': -459.67, 'cmap': 'RdYlBu_r'},
        'DPT': {'name': 'Dew Point', 'units': '°F', 'multiplier': 1.8, 'offset': -459.67, 'cmap': 'Blues'},
        'RH': {'name': 'Relative Humidity', 'units': '%', 'multiplier': 1, 'cmap': 'Blues'},
        'PRES': {'name': 'Pressure', 'units': 'hPa', 'multiplier': 0.01, 'cmap': 'viridis'},
        'PRMSL': {'name': 'Sea Level Pressure', 'units': 'hPa', 'multiplier': 0.01, 'cmap': 'viridis'},
        'APCP': {'name': 'Precipitation', 'units': 'mm', 'multiplier': 1, 'cmap': 'Blues'},
        'VIS': {'name': 'Visibility', 'units': 'km', 'multiplier': 0.001, 'cmap': 'viridis'},
        'TCDC': {'name': 'Total Cloud Cover', 'units': '%', 'multiplier': 1, 'cmap': 'gray'},
        'HGT': {'name': 'Geopotential Height', 'units': 'm', 'multiplier': 1, 'cmap': 'terrain'},
    }
    
    # Pressure levels available in 3DRTMA data
    PRESSURE_LEVELS = [
        50, 75, 100, 125, 150, 175, 200, 225, 250, 275, 300, 325, 350, 375, 400, 
        425, 450, 475, 500, 525, 550, 575, 600, 625, 650, 675, 700, 725, 750, 
        775, 800, 825, 850, 875, 900, 925, 950, 975, 1000
    ]
    
    # Common pressure levels with names
    COMMON_PRESSURE_LEVELS = {
        0: 'Surface Level',
        50: '50 mb (~20 km, Lower Stratosphere)',
        100: '100 mb (~16 km, Tropopause)',
        200: '200 mb (~12 km, Upper Troposphere)', 
        300: '300 mb (~9 km, Jet Stream Level)',
        500: '500 mb (~5.5 km, Mid-Troposphere)',
        700: '700 mb (~3 km, Lower Troposphere)',
        850: '850 mb (~1.5 km, Boundary Layer)',
        925: '925 mb (~750 m, Near Surface)',
        1000: '1000 mb (Sea Level)'
    }
    
    # Map settings
    DEFAULT_ZOOM = 6
    DEFAULT_OPACITY = 0.6
    CONTOUR_LEVELS = 20
    
    # Figure settings
    FIGURE_SIZE = (12, 8)
    FIGURE_DPI = 150


class GRIBDataProcessor:
    """Handles GRIB2 data downloading and processing."""
    
    def __init__(self, config: WeatherMapConfig):
        self.config = config
        self.session = requests.Session()
        
    def get_grib_inventory(self, idx_url: str) -> List[Dict[str, Any]]:
        """Parse GRIB2 index file to find all variables."""
        try:
            logger.info(f"Fetching GRIB inventory from: {idx_url}")
            response = self.session.get(idx_url, timeout=30)
            response.raise_for_status()
            
            lines = response.text.strip().split('\n')
            inventory = []
            
            for i, line in enumerate(lines):
                parts = line.split(':')
                if len(parts) >= 7:
                    record_num = int(parts[0])
                    byte_start = int(parts[1])
                    
                    # Get byte end from next record or file size
                    if i < len(lines) - 1:
                        next_parts = lines[i + 1].split(':')
                        byte_end = int(next_parts[1]) - 1
                    else:
                        byte_end = None
                    
                    inventory.append({
                        'record': record_num,
                        'byte_start': byte_start,
                        'byte_end': byte_end,
                        'variable': parts[3],
                        'level': parts[4],
                        'forecast_time': parts[5],
                        'full_line': line
                    })
            
            logger.info(f"Found {len(inventory)} records in inventory")
            return inventory
            
        except requests.RequestException as e:
            logger.error(f"Failed to fetch GRIB inventory: {e}")
            raise
        except Exception as e:
            logger.error(f"Error parsing GRIB inventory: {e}")
            raise
    
    def download_grib_subset(self, grib_url: str, byte_start: int, byte_end: Optional[int]) -> bytes:
        """Download specific bytes from GRIB2 file."""
        try:
            headers = {'Range': f'bytes={byte_start}-{byte_end}'} if byte_end else {'Range': f'bytes={byte_start}-'}
            response = self.session.get(grib_url, headers=headers, timeout=60)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            logger.error(f"Failed to download GRIB subset: {e}")
            raise
    
    def get_variable_info(self, variable_name: str) -> Dict[str, Any]:
        """Get display information for meteorological variables."""
        default = {'name': variable_name, 'units': 'raw', 'multiplier': 1, 'cmap': 'viridis'}
        return self.config.VARIABLE_INFO.get(variable_name, default)
    
    def load_single_variable(self, grib_url: str, idx_url: str, variable_name: str, pressure_level: Optional[int] = None) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, np.ndarray]]]:
        """Load a single variable from the GRIB2 file using byte slicing."""
        level_msg = f" at {pressure_level}mb" if pressure_level and pressure_level > 0 else " at surface" if pressure_level == 0 else ""
        logger.info(f"Loading single variable: {variable_name}{level_msg}")
        
        inventory = self.get_grib_inventory(idx_url)
        
        # Find the specific variable
        target_record = None
        for record in inventory:
            if record['variable'] == variable_name:
                # For 3DRTMA data, check pressure level if specified
                if pressure_level is not None:
                    level_str = record['level']
                    if pressure_level == 0:
                        # Surface level - look for "surface" or "sfc" in level string
                        if 'surface' in level_str.lower() or 'sfc' in level_str.lower():
                            target_record = record
                            break
                    else:
                        # Pressure level format in 3DRTMA is typically "pressure mb" 
                        if f"{pressure_level} mb" in level_str or f"{pressure_level}mb" in level_str:
                            target_record = record
                            break
                else:
                    # For RTMA data or when no pressure level specified, take first match
                    target_record = record
                    break
        
        if target_record is None:
            level_msg = f" at {pressure_level}mb" if pressure_level and pressure_level > 0 else " at surface" if pressure_level == 0 else ""
            logger.error(f"Variable {variable_name}{level_msg} not found in inventory")
            
            # Log available records for debugging
            if pressure_level is not None:
                matching_vars = [r for r in inventory if r['variable'] == variable_name]
                if matching_vars:
                    available_levels = [r['level'] for r in matching_vars]
                    logger.info(f"Available levels for {variable_name}: {available_levels}")
                else:
                    available_vars = list(set(r['variable'] for r in inventory))
                    logger.info(f"Variable {variable_name} not found. Available variables: {available_vars[:10]}...")
            
            return None, None
        
        try:
            logger.info(f"Downloading {variable_name} data...")
            
            # Download the specific record
            grib_data = self.download_grib_subset(grib_url, target_record['byte_start'], target_record['byte_end'])
            
            # Process with temporary file
            with tempfile.NamedTemporaryFile(suffix='.grb2', delete=False) as temp_file:
                temp_file.write(grib_data)
                temp_file_path = temp_file.name
            
            try:
                # Read with xarray/cfgrib
                ds = xr.open_dataset(temp_file_path, engine='cfgrib')
                
                data_vars = list(ds.data_vars)
                if data_vars:
                    var_data = ds[data_vars[0]]
                    
                    # Extract coordinates
                    coords = self._extract_coordinates(ds)
                    
                    # Get variable info and convert units
                    var_info = self.get_variable_info(variable_name)
                    converted_data = self._convert_units(var_data, var_info)
                    
                    variable_data = {
                        'data': converted_data,
                        'info': var_info,
                        'raw_data': var_data
                    }
                    
                    logger.info(f"  {variable_name}: {var_info['name']} ({var_info['units']}) - "
                              f"Range: {float(converted_data.min()):.2f} to {float(converted_data.max()):.2f}")
                    
                    return variable_data, coords
                
            finally:
                # Clean up temporary file
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
                    
        except Exception as e:
            error_msg = str(e)
            if "JPEG support not enabled" in error_msg or "Functionality not enabled" in error_msg:
                logger.error(f"Error loading {variable_name}: JPEG compression not supported. "
                           f"3DRTMA data requires eccodes with JPEG support. Error: {e}")
            else:
                logger.error(f"Error loading {variable_name}: {e}")
            return None, None
        
        return None, None

    def get_available_variables(self, idx_url: str) -> List[str]:
        """Get list of available variables from the GRIB index."""
        try:
            inventory = self.get_grib_inventory(idx_url)
            variables = list(set(record['variable'] for record in inventory))
            return sorted(variables)
        except Exception as e:
            logger.error(f"Error getting available variables: {e}")
            return []

    def load_all_variables(self, grib_url: str, idx_url: str) -> Tuple[Dict[str, Any], Optional[Dict[str, np.ndarray]]]:
        """Load all variables from the GRIB2 file."""
        logger.info("Starting variable loading process")
        
        inventory = self.get_grib_inventory(idx_url)
        
        # Group by variable name
        variables_by_name = {}
        for record in inventory:
            var_name = record['variable']
            if var_name not in variables_by_name:
                variables_by_name[var_name] = []
            variables_by_name[var_name].append(record)
        
        logger.info(f"Available variables: {list(variables_by_name.keys())}")
        
        all_data = {}
        coords = None
        
        for var_name, records in variables_by_name.items():
            try:
                record = records[0]  # Use first record for each variable
                logger.info(f"Loading {var_name}...")
                
                # Download the specific record
                grib_data = self.download_grib_subset(grib_url, record['byte_start'], record['byte_end'])
                
                # Process with temporary file
                with tempfile.NamedTemporaryFile(suffix='.grb2', delete=False) as temp_file:
                    temp_file.write(grib_data)
                    temp_file_path = temp_file.name
                
                try:
                    # Read with xarray/cfgrib
                    ds = xr.open_dataset(temp_file_path, engine='cfgrib')
                    
                    data_vars = list(ds.data_vars)
                    if data_vars:
                        var_data = ds[data_vars[0]]
                        
                        # Store coordinates from first successful load
                        if coords is None:
                            coords = self._extract_coordinates(ds)
                        
                        # Get variable info and convert units
                        var_info = self.get_variable_info(var_name)
                        converted_data = self._convert_units(var_data, var_info)
                        
                        all_data[var_name] = {
                            'data': converted_data,
                            'info': var_info,
                            'raw_data': var_data,
                            'records': records
                        }
                        
                        logger.info(f"  {var_name}: {var_info['name']} ({var_info['units']}) - "
                                  f"Range: {float(converted_data.min()):.2f} to {float(converted_data.max()):.2f}")
                
                finally:
                    # Clean up temporary file
                    if os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
                        
            except Exception as e:
                logger.warning(f"Error loading {var_name}: {e}")
                continue
        
        if not all_data:
            logger.error("No variables could be loaded successfully")
            return {}, None
            
        logger.info(f"Successfully loaded {len(all_data)} variables")
        return all_data, coords
    
    def _extract_coordinates(self, ds: xr.Dataset) -> Dict[str, np.ndarray]:
        """Extract and process coordinate grids from dataset."""
        lats = ds.latitude.values
        lons = ds.longitude.values
        
        # Convert to regular grid if needed
        if len(lats.shape) == 1:
            lon_grid, lat_grid = np.meshgrid(lons, lats)
        else:
            lat_grid, lon_grid = lats, lons
        
        # Adjust longitude if needed (convert from 0-360 to -180-180)
        if lon_grid.max() > 180:
            lon_grid = np.where(lon_grid > 180, lon_grid - 360, lon_grid)
        
        return {'lat_grid': lat_grid, 'lon_grid': lon_grid}
    
    def _convert_units(self, var_data: xr.DataArray, var_info: Dict[str, Any]) -> xr.DataArray:
        """Convert variable data to appropriate units."""
        converted_data = var_data * var_info['multiplier']
        if 'offset' in var_info:
            converted_data += var_info['offset']
        return converted_data


class WeatherMapRenderer:
    """Handles map visualization and HTML generation."""
    
    def __init__(self, config: WeatherMapConfig):
        self.config = config
        
    def create_contour_overlay(self, lon_grid: np.ndarray, lat_grid: np.ndarray, 
                             data: np.ndarray, levels: Optional[np.ndarray] = None, 
                             cmap: str = 'YlOrRd', opacity: float = 0.6) -> str:
        """Create a contour overlay as a raster image for Folium."""
        
        # Create figure with transparent background
        fig, ax = plt.subplots(figsize=self.config.FIGURE_SIZE, dpi=self.config.FIGURE_DPI)
        fig.patch.set_alpha(0)
        ax.set_facecolor('none')
        
        # Create contour plot
        if levels is None:
            levels = np.linspace(np.nanmin(data), np.nanmax(data), self.config.CONTOUR_LEVELS)
        
        contour = ax.contourf(lon_grid, lat_grid, data, levels=levels, cmap=cmap, alpha=opacity)
        
        # Remove axes and margins
        ax.set_xlim(lon_grid.min(), lon_grid.max())
        ax.set_ylim(lat_grid.min(), lat_grid.max())
        ax.axis('off')
        plt.tight_layout(pad=0)
        
        # Save to bytes
        buf = io.BytesIO()
        plt.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0, 
                   dpi=self.config.FIGURE_DPI)
        buf.seek(0)
        plt.close(fig)
        
        # Convert to base64
        img_data = base64.b64encode(buf.getvalue()).decode()
        buf.close()
        
        return img_data
    
    def create_single_variable_map(self, variable_data: Dict[str, Any], 
                                 coords: Dict[str, np.ndarray], 
                                 variable_name: str,
                                 available_variables: List[str],
                                 date: str, hour: int,
                                 data_source: str = 'RTMA') -> folium.Map:
        """Create interactive map with single variable and AJAX variable switching."""
        
        lat_grid = coords['lat_grid']
        lon_grid = coords['lon_grid']
        
        # Create base map centered on Salt Lake City, Utah
        center_lat = 40.7608  # Salt Lake City latitude
        center_lon = -111.8910  # Salt Lake City longitude
        
        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=self.config.DEFAULT_ZOOM,
            tiles=None
        )
        
        # Add base layers
        self._add_base_layers(m)
        
        # Get bounds for image overlays
        bounds = [[float(lat_grid.min()), float(lon_grid.min())], 
                  [float(lat_grid.max()), float(lon_grid.max())]]
        
        # Create current variable overlay
        data = variable_data['data']
        var_info = variable_data['info']
        
        # Create contour levels
        vmin, vmax = float(data.min()), float(data.max())
        levels = np.linspace(vmin, vmax, self.config.CONTOUR_LEVELS)
        
        # Create contour overlay
        logger.info(f"Creating contour overlay for {variable_name}...")
        img_data = self.create_contour_overlay(lon_grid, lat_grid, data, 
                                             levels=levels, cmap=var_info['cmap'])
        
        # Create image overlay
        img_overlay = folium.raster_layers.ImageOverlay(
            image=f'data:image/png;base64,{img_data}',
            bounds=bounds,
            opacity=self.config.DEFAULT_OPACITY,
            interactive=False,
            cross_origin=False,
            zindex=1,
            name='weather_overlay'
        )
        
        img_overlay.add_to(m)
        
        # Add layer control
        folium.LayerControl().add_to(m)
        
        # Variable info for current variable
        variable_info = {
            'name': var_info['name'],
            'units': var_info['units'],
            'min': vmin,
            'max': vmax,
            'cmap': var_info['cmap']
        }
        
        # Add simple opacity control panel
        self._add_opacity_control(m)
        
        return m

    def create_multi_variable_map(self, all_data: Dict[str, Any], 
                                coords: Dict[str, np.ndarray]) -> folium.Map:
        """Create interactive map with all variables and dropdown selector."""
        
        lat_grid = coords['lat_grid']
        lon_grid = coords['lon_grid']
        
        # Create base map centered on Salt Lake City, Utah
        center_lat = 40.7608  # Salt Lake City latitude
        center_lon = -111.8910  # Salt Lake City longitude
        
        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=self.config.DEFAULT_ZOOM,
            tiles=None
        )
        
        # Add base layers
        self._add_base_layers(m)
        
        # Get bounds for image overlays
        bounds = [[float(lat_grid.min()), float(lon_grid.min())], 
                  [float(lat_grid.max()), float(lon_grid.max())]]
        
        # Create image overlays for each variable
        variable_overlays = {}
        variable_info_json = {}
        
        for var_name, var_data in all_data.items():
            data = var_data['data']
            var_info = var_data['info']
            
            # Create contour levels
            vmin, vmax = float(data.min()), float(data.max())
            levels = np.linspace(vmin, vmax, self.config.CONTOUR_LEVELS)
            
            # Create contour overlay
            logger.info(f"Creating contour overlay for {var_name}...")
            img_data = self.create_contour_overlay(lon_grid, lat_grid, data, 
                                                 levels=levels, cmap=var_info['cmap'])
            
            # Create image overlay
            img_overlay = folium.raster_layers.ImageOverlay(
                image=f'data:image/png;base64,{img_data}',
                bounds=bounds,
                opacity=self.config.DEFAULT_OPACITY,
                interactive=False,
                cross_origin=False,
                zindex=1,
                name=f'{var_name}_overlay'
            )
            
            variable_overlays[var_name] = img_overlay
            variable_info_json[var_name] = {
                'name': var_info['name'],
                'units': var_info['units'],
                'min': vmin,
                'max': vmax,
                'cmap': var_info['cmap']
            }
        
        # Add the first variable by default
        if variable_overlays:
            first_var = list(variable_overlays.keys())[0]
            variable_overlays[first_var].add_to(m)
        
        # Add layer control
        folium.LayerControl().add_to(m)
        
        # Add control panel
        self._add_control_panel(m, all_data, variable_info_json, first_var)
        
        return m
    
    def _add_base_layers(self, m: folium.Map) -> None:
        """Add base map layers."""
        folium.TileLayer('OpenStreetMap', name='OpenStreetMap').add_to(m)
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='Esri',
            name='Satellite',
            overlay=False,
            control=True
        ).add_to(m)
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
            attr='Esri',
            name='Topographic',
            overlay=False,
            control=True
        ).add_to(m)
    
    def _add_ajax_control_panel(self, m: folium.Map, current_variable: str, 
                               variable_info: Dict[str, Any], 
                               available_variables: List[str],
                               date: str, hour: int, data_source: str = 'RTMA') -> None:
        """Add interactive control panel with AJAX variable switching."""
        
        # Create dropdown options
        dropdown_options = ""
        for var_name in available_variables:
            selected = "selected" if var_name == current_variable else ""
            var_display = self.config.VARIABLE_INFO.get(var_name, {}).get('name', var_name)
            dropdown_options += f'<option value="{var_name}" {selected}>{var_display} ({var_name})</option>\n'
        
        # Control panel HTML with AJAX functionality
        control_panel_html = f'''
        <div id="controlPanel" style="position: fixed; 
                    top: 10px; right: 10px; width: 320px; 
                    background-color: white; border:2px solid grey; z-index:9999; 
                    font-size:12px; padding: 15px; border-radius: 5px;">
        
        <!-- Variable Selector -->
        <div style="margin-bottom: 15px;">
            <label for="variableSelect" style="font-weight: bold; display: block; margin-bottom: 5px;">
                Select Variable:
            </label>
            <div style="display: flex; gap: 10px;">
                <select id="variableSelect" style="flex: 1; padding: 5px;">
                    {dropdown_options}
                </select>
                <button id="applyVariable" onclick="applyVariableChange()" 
                        style="padding: 5px 15px; background-color: #3498db; color: white; border: none; border-radius: 3px; cursor: pointer;">
                    Apply
                </button>
            </div>
            <div id="loadingIndicator" style="display: none; margin-top: 5px; color: #666; font-style: italic;">
                Loading new variable...
            </div>
            <div id="debugInfo" style="display: none; margin-top: 5px; font-size: 10px; color: #888; background: #f0f0f0; padding: 5px; border-radius: 3px;">
                Debug info will appear here
            </div>
        </div>
        
        <!-- Variable Info -->
        <div id="variableInfo" style="margin-bottom: 15px; padding: 10px; background-color: #f5f5f5; border-radius: 3px;">
            <div id="variableName" style="font-weight: bold;"></div>
            <div id="variableRange" style="font-size: 11px; color: #666;"></div>
        </div>
        
        <!-- Color Scale -->
        <div style="margin-bottom: 15px;">
            <div style="font-weight: bold; margin-bottom: 5px;">Color Scale:</div>
            <div id="colorScale" style="height: 20px; width: 100%; border: 1px solid #ccc; border-radius: 3px;"></div>
            <div style="display: flex; justify-content: space-between; margin-top: 5px; font-size: 10px;">
                <span id="minValue"></span>
                <span id="maxValue"></span>
            </div>
        </div>
        
        <!-- Opacity Slider -->
        <div>
            <label for="opacitySlider" style="font-weight: bold; display: block; margin-bottom: 5px;">
                Layer Opacity: <span id="opacityValue">60%</span>
            </label>
            <input type="range" id="opacitySlider" min="0" max="100" value="60" 
                   style="width: 100%;" oninput="updateOpacity(this.value)">
        </div>
        </div>
        
        <script>
        // Current state
        var currentVariable = '{current_variable}';
        var currentDate = '{date}';
        var currentHour = {hour};
        var currentDataSource = '{data_source}';
        var map = window['{m.get_name()}'];  // Get the map from window object
        var currentOverlay = null;
        
        // Wait for map to be fully loaded
        document.addEventListener('DOMContentLoaded', function() {{
            // Try to get map reference multiple ways
            if (typeof map === 'undefined') {{
                map = window['{m.get_name()}'];
            }}
            if (typeof map === 'undefined') {{
                // Find map in Folium's internal structure
                var mapKeys = Object.keys(window).filter(key => key.startsWith('map_'));
                if (mapKeys.length > 0) {{
                    map = window[mapKeys[0]];
                }}
            }}
            
            console.log('Map object:', map);
            if (map) {{
                console.log('Map successfully loaded');
            }} else {{
                console.error('Map not found');
            }}
        }});
        
        // Current variable info
        var currentVariableInfo = {json.dumps(variable_info)};
        
        // Color map gradients
        var colormaps = {{
            'YlOrRd': 'linear-gradient(to right, #ffffcc 0%, #ffeda0 12.5%, #fed976 25%, #feb24c 37.5%, #fd8d3c 50%, #fc4e2a 62.5%, #e31a1c 75%, #bd0026 87.5%, #800026 100%)',
            'RdBu_r': 'linear-gradient(to right, #67001f 0%, #b2182b 16.7%, #d6604d 33.3%, #f4a582 50%, #fddbc7 66.7%, #d1e5f0 83.3%, #92c5de 100%)',
            'plasma': 'linear-gradient(to right, #0d0887 0%, #46039f 16.7%, #7201a8 33.3%, #9c179e 50%, #bd3786 66.7%, #d8576b 83.3%, #ed7953 100%)',
            'RdYlBu_r': 'linear-gradient(to right, #a50026 0%, #d73027 16.7%, #f46d43 33.3%, #fdae61 50%, #fee090 66.7%, #abd9e9 83.3%, #74add1 100%)',
            'Blues': 'linear-gradient(to right, #f7fbff 0%, #deebf7 16.7%, #c6dbef 33.3%, #9ecae1 50%, #6baed6 66.7%, #4292c6 83.3%, #08519c 100%)',
            'viridis': 'linear-gradient(to right, #440154 0%, #482777 16.7%, #3f4a8a 33.3%, #31678e 50%, #26838f 66.7%, #6cce5a 83.3%, #b6de2b 100%)',
            'gray': 'linear-gradient(to right, #000000 0%, #404040 33.3%, #808080 66.7%, #ffffff 100%)'
        }};
        
        // Find current overlay on map
        function findCurrentOverlay() {{
            var overlay = null;
            if (!map || typeof map.eachLayer !== 'function') {{
                console.error('Map not available or eachLayer method not found');
                showDebugInfo('Map not available for overlay management');
                return null;
            }}
            
            try {{
                map.eachLayer(function(layer) {{
                    if (layer.options && layer.options.name === 'weather_overlay') {{
                        overlay = layer;
                    }}
                }});
            }} catch (e) {{
                console.error('Error finding overlay:', e);
                showDebugInfo('Error finding overlay: ' + e.message);
            }}
            return overlay;
        }}
        
        function waitForMap(callback, maxAttempts = 10) {{
            var attempts = 0;
            function checkMap() {{
                if (map && typeof map.eachLayer === 'function') {{
                    callback();
                }} else if (attempts < maxAttempts) {{
                    attempts++;
                    showDebugInfo('Waiting for map... attempt ' + attempts);
                    setTimeout(checkMap, 500);
                }} else {{
                    showDebugInfo('Map failed to load after ' + maxAttempts + ' attempts');
                    alert('Map not properly loaded. Please refresh the page.');
                }}
            }}
            checkMap();
        }}
        
        function updateVariableDisplay() {{
            var varData = currentVariableInfo;
            document.getElementById('variableName').textContent = varData.name;
            document.getElementById('variableRange').textContent = 
                'Range: ' + varData.min.toFixed(2) + ' - ' + varData.max.toFixed(2) + ' ' + varData.units;
            document.getElementById('minValue').textContent = varData.min.toFixed(1) + ' ' + varData.units;
            document.getElementById('maxValue').textContent = varData.max.toFixed(1) + ' ' + varData.units;
            
            var gradient = colormaps[varData.cmap] || colormaps['viridis'];
            document.getElementById('colorScale').style.background = gradient;
        }}
        
        function showDebugInfo(message) {{
            var debugDiv = document.getElementById('debugInfo');
            debugDiv.textContent = new Date().toLocaleTimeString() + ': ' + message;
            debugDiv.style.display = 'block';
            setTimeout(function() {{
                debugDiv.style.display = 'none';
            }}, 10000); // Hide after 10 seconds
        }}
        
        function applyVariableChange() {{
            var newVariable = document.getElementById('variableSelect').value;
            if (newVariable === currentVariable) {{
                showDebugInfo('Variable already selected: ' + newVariable);
                return;
            }}
            
            // Check if map is available
            if (!map || typeof map.eachLayer !== 'function') {{
                showDebugInfo('Map not ready, waiting...');
                waitForMap(function() {{
                    applyVariableChange();
                }});
                return;
            }}
            
            // Show loading indicator
            document.getElementById('loadingIndicator').style.display = 'block';
            document.getElementById('applyVariable').disabled = true;
            document.getElementById('variableSelect').disabled = true;
            
            // Prepare date - ensure it's in the correct format
            var dateToSend = currentDate;
            showDebugInfo('Original date: ' + currentDate);
            
            // If date contains dashes, remove them to convert YYYY-MM-DD to YYYYMMDD
            if (currentDate.includes('-')) {{
                dateToSend = currentDate.replace(/-/g, '');
                showDebugInfo('Converted date: ' + dateToSend);
            }}
            
            var requestData = {{
                date: dateToSend,
                hour: currentHour,
                variable: newVariable,
                data_source: currentDataSource
            }};
            
            showDebugInfo('Sending request: ' + JSON.stringify(requestData));
            
            // Make AJAX request to get new variable data
            fetch('/get_variable_data', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/json',
                }},
                body: JSON.stringify(requestData)
            }})
            .then(response => {{
                showDebugInfo('Response status: ' + response.status);
                if (!response.ok) {{
                    throw new Error('HTTP ' + response.status);
                }}
                return response.json();
            }})
            .then(data => {{
                showDebugInfo('Response data: ' + JSON.stringify({{success: data.success, error: data.error || 'none'}}));
                
                if (data.success) {{
                    // Remove current overlay
                    var oldOverlay = findCurrentOverlay();
                    if (oldOverlay) {{
                        try {{
                            map.removeLayer(oldOverlay);
                            showDebugInfo('Removed old overlay');
                        }} catch (e) {{
                            console.error('Error removing overlay:', e);
                            showDebugInfo('Error removing overlay: ' + e.message);
                        }}
                    }}
                    
                    // Add new overlay - use Leaflet directly
                    try {{
                        var newOverlay = L.imageOverlay(
                            'data:image/png;base64,' + data.image_data,
                            data.bounds,
                            {{
                                opacity: document.getElementById('opacitySlider').value / 100,
                                interactive: false,
                                crossOrigin: false,
                                zIndex: 1,
                                name: 'weather_overlay'
                            }}
                        );
                        
                        if (map && typeof map.addLayer === 'function') {{
                            newOverlay.addTo(map);
                            showDebugInfo('Added new overlay for ' + newVariable);
                        }} else {{
                            throw new Error('Map.addLayer not available');
                        }}
                        
                        // Update variable info
                        currentVariable = newVariable;
                        currentVariableInfo = data.variable_info;
                        updateVariableDisplay();
                        
                        showDebugInfo('Successfully loaded ' + newVariable);
                        
                    }} catch (e) {{
                        console.error('Error adding overlay:', e);
                        showDebugInfo('Error adding overlay: ' + e.message);
                        alert('Error displaying new variable overlay: ' + e.message);
                    }}
                    
                }} else {{
                    var errorMsg = 'Error loading variable: ' + (data.error || 'Unknown error');
                    alert(errorMsg);
                    showDebugInfo(errorMsg);
                    // Reset dropdown to previous selection
                    document.getElementById('variableSelect').value = currentVariable;
                }}
            }})
            .catch(error => {{
                console.error('Error:', error);
                var errorMsg = 'Network error loading variable data: ' + error.message;
                alert(errorMsg);
                showDebugInfo(errorMsg);
                document.getElementById('variableSelect').value = currentVariable;
            }})
            .finally(() => {{
                // Hide loading indicator and re-enable controls
                document.getElementById('loadingIndicator').style.display = 'none';
                document.getElementById('applyVariable').disabled = false;
                document.getElementById('variableSelect').disabled = false;
                showDebugInfo('Request completed');
            }});
        }}
        
        // Legacy function for backward compatibility (if needed)
        function changeVariable() {{
            // Just update the display, don't make the request
            // User must click Apply button
            showDebugInfo('Variable selection changed - click Apply to load');
        }}
        
        function updateOpacity(value) {{
            var opacity = value / 100;
            var overlay = findCurrentOverlay();
            if (overlay) {{
                overlay.setOpacity(opacity);
            }}
            document.getElementById('opacityValue').textContent = value + '%';
        }}
        
        // Initialize display
        updateVariableDisplay();
        </script>
        '''
        
        m.get_root().html.add_child(folium.Element(control_panel_html))
    
    def _add_opacity_control(self, m: folium.Map) -> None:
        """Add simple opacity control to map."""
        
        # Simple opacity control panel HTML
        opacity_control_html = f'''
        <div id="opacityPanel" style="position: fixed; 
                    top: 10px; right: 10px; width: 200px; 
                    background-color: white; border:2px solid grey; z-index:9999; 
                    font-size:12px; padding: 15px; border-radius: 5px;">
        
        <!-- Opacity Slider -->
        <div>
            <label for="opacitySlider" style="font-weight: bold; display: block; margin-bottom: 5px;">
                Layer Opacity: <span id="opacityValue">60%</span>
            </label>
            <input type="range" id="opacitySlider" min="0" max="100" value="60" 
                   style="width: 100%;" oninput="updateOpacity(this.value)">
        </div>
        </div>
        
        <script>
        // Get map reference
        var map = window['{m.get_name()}'];
        
        // Find current overlay on map
        function findCurrentOverlay() {{
            var overlay = null;
            if (!map || typeof map.eachLayer !== 'function') {{
                console.error('Map not available or eachLayer method not found');
                return null;
            }}
            
            try {{
                map.eachLayer(function(layer) {{
                    if (layer.options && layer.options.name === 'weather_overlay') {{
                        overlay = layer;
                    }}
                }});
            }} catch (e) {{
                console.error('Error finding overlay:', e);
            }}
            return overlay;
        }}
        
        function updateOpacity(value) {{
            var opacity = value / 100;
            var overlay = findCurrentOverlay();
            if (overlay) {{
                overlay.setOpacity(opacity);
            }}
            document.getElementById('opacityValue').textContent = value + '%';
        }}
        </script>
        '''
        
        m.get_root().html.add_child(folium.Element(opacity_control_html))
    
    def _add_control_panel(self, m: folium.Map, all_data: Dict[str, Any], 
                          variable_info_json: Dict[str, Any], first_var: str) -> None:
        """Add interactive control panel to map."""
        
        # Create dropdown options
        dropdown_options = ""
        for i, (var_name, var_data) in enumerate(all_data.items()):
            selected = "selected" if i == 0 else ""
            dropdown_options += f'<option value="{var_name}" {selected}>{var_data["info"]["name"]} ({var_name})</option>\n'
        
        # Control panel HTML
        control_panel_html = f'''
        <div id="controlPanel" style="position: fixed; 
                    top: 10px; right: 10px; width: 320px; 
                    background-color: white; border:2px solid grey; z-index:9999; 
                    font-size:12px; padding: 15px; border-radius: 5px;">
        
        <!-- Variable Selector -->
        <div style="margin-bottom: 15px;">
            <label for="variableSelect" style="font-weight: bold; display: block; margin-bottom: 5px;">
                Select Variable:
            </label>
            <select id="variableSelect" style="width: 100%; padding: 5px;" onchange="changeVariable()">
                {dropdown_options}
            </select>
        </div>
        
        <!-- Variable Info -->
        <div id="variableInfo" style="margin-bottom: 15px; padding: 10px; background-color: #f5f5f5; border-radius: 3px;">
            <div id="variableName" style="font-weight: bold;"></div>
            <div id="variableRange" style="font-size: 11px; color: #666;"></div>
        </div>
        
        <!-- Color Scale -->
        <div style="margin-bottom: 15px;">
            <div style="font-weight: bold; margin-bottom: 5px;">Color Scale:</div>
            <div id="colorScale" style="height: 20px; width: 100%; border: 1px solid #ccc; border-radius: 3px;"></div>
            <div style="display: flex; justify-content: space-between; margin-top: 5px; font-size: 10px;">
                <span id="minValue"></span>
                <span id="maxValue"></span>
            </div>
        </div>
        
        <!-- Opacity Slider -->
        <div>
            <label for="opacitySlider" style="font-weight: bold; display: block; margin-bottom: 5px;">
                Layer Opacity: <span id="opacityValue">60%</span>
            </label>
            <input type="range" id="opacitySlider" min="0" max="100" value="60" 
                   style="width: 100%;" oninput="updateOpacity(this.value)">
        </div>
        </div>
        
        <script>
        // Store variable data
        var variableData = {json.dumps(variable_info_json)};
        var currentVariable = '{first_var}';
        var map = {m.get_name()};
        
        // Color map gradients
        var colormaps = {{
            'YlOrRd': 'linear-gradient(to right, #ffffcc 0%, #ffeda0 12.5%, #fed976 25%, #feb24c 37.5%, #fd8d3c 50%, #fc4e2a 62.5%, #e31a1c 75%, #bd0026 87.5%, #800026 100%)',
            'RdBu_r': 'linear-gradient(to right, #67001f 0%, #b2182b 16.7%, #d6604d 33.3%, #f4a582 50%, #fddbc7 66.7%, #d1e5f0 83.3%, #92c5de 100%)',
            'plasma': 'linear-gradient(to right, #0d0887 0%, #46039f 16.7%, #7201a8 33.3%, #9c179e 50%, #bd3786 66.7%, #d8576b 83.3%, #ed7953 100%)',
            'RdYlBu_r': 'linear-gradient(to right, #a50026 0%, #d73027 16.7%, #f46d43 33.3%, #fdae61 50%, #fee090 66.7%, #abd9e9 83.3%, #74add1 100%)',
            'Blues': 'linear-gradient(to right, #f7fbff 0%, #deebf7 16.7%, #c6dbef 33.3%, #9ecae1 50%, #6baed6 66.7%, #4292c6 83.3%, #08519c 100%)',
            'viridis': 'linear-gradient(to right, #440154 0%, #482777 16.7%, #3f4a8a 33.3%, #31678e 50%, #26838f 66.7%, #6cce5a 83.3%, #b6de2b 100%)',
            'gray': 'linear-gradient(to right, #000000 0%, #404040 33.3%, #808080 66.7%, #ffffff 100%)'
        }};
        
        function updateVariableDisplay() {{
            var varData = variableData[currentVariable];
            document.getElementById('variableName').textContent = varData.name;
            document.getElementById('variableRange').textContent = 
                'Range: ' + varData.min.toFixed(2) + ' - ' + varData.max.toFixed(2) + ' ' + varData.units;
            document.getElementById('minValue').textContent = varData.min.toFixed(1) + ' ' + varData.units;
            document.getElementById('maxValue').textContent = varData.max.toFixed(1) + ' ' + varData.units;
            
            var gradient = colormaps[varData.cmap] || colormaps['viridis'];
            document.getElementById('colorScale').style.background = gradient;
        }}
        
        function changeVariable() {{
            var newVariable = document.getElementById('variableSelect').value;
            currentVariable = newVariable;
            updateVariableDisplay();
            alert('Variable changed to: ' + variableData[newVariable].name + 
                  '\\nNote: Full overlay switching requires additional implementation.');
        }}
        
        function updateOpacity(value) {{
            var opacity = value / 100;
            map.eachLayer(function(layer) {{
                if (layer.options && layer.options.name === currentVariable + '_overlay') {{
                    layer.setOpacity(opacity);
                }}
            }});
            document.getElementById('opacityValue').textContent = value + '%';
        }}
        
        // Initialize display
        updateVariableDisplay();
        </script>
        '''
        
        m.get_root().html.add_child(folium.Element(control_panel_html))


class WeatherMapGenerator:
    """Main application class."""
    
    def __init__(self):
        self.config = WeatherMapConfig()
        self.processor = GRIBDataProcessor(self.config)
        self.renderer = WeatherMapRenderer(self.config)
    
    def generate_urls(self, date: str, hour: int, data_source: str = None) -> Tuple[str, str]:
        """Generate GRIB and index URLs for given date, hour, and data source."""
        if data_source is None:
            data_source = self.config.DEFAULT_DATA_SOURCE
            
        if data_source not in self.config.DATA_SOURCES:
            raise ValueError(f"Unknown data source: {data_source}. Available: {list(self.config.DATA_SOURCES.keys())}")
        
        source_config = self.config.DATA_SOURCES[data_source]
        
        grib_url = source_config['grib_pattern'].format(
            base_url=source_config['base_url'], date=date, hour=hour
        )
        idx_url = source_config['idx_pattern'].format(
            base_url=source_config['base_url'], date=date, hour=hour
        )
        return grib_url, idx_url
    
    def get_available_pressure_levels(self, date: str, hour: int, data_source: str) -> List[int]:
        """Get available pressure levels for pressure-enabled data sources."""
        source_config = self.config.DATA_SOURCES.get(data_source, {})
        if not source_config.get('has_pressure_levels', False):
            return []
        
        try:
            grib_url, idx_url = self.generate_urls(date, hour, data_source)
            inventory = self.processor.get_grib_inventory(idx_url)
            
            pressure_levels = set()
            has_surface = False
            
            for record in inventory:
                level_str = record['level']
                # Check for surface level first
                if 'surface' in level_str.lower() or 'sfc' in level_str.lower():
                    has_surface = True
                # Extract pressure level from strings like "500 mb" 
                elif 'mb' in level_str:
                    parts = level_str.split()
                    for part in parts:
                        if part.isdigit():
                            pressure_levels.add(int(part))
                            break
            
            levels = sorted(list(pressure_levels))
            # Add surface level as 0 mb if it exists
            if has_surface:
                levels.insert(0, 0)
                
            return levels
        except Exception as e:
            logger.error(f"Error getting pressure levels: {e}")
            return []
    
    def get_filtered_variables(self, date: str, hour: int, data_source: str) -> List[str]:
        """Get available variables, filtered for data source compatibility."""
        try:
            grib_url, idx_url = self.generate_urls(date, hour, data_source)
            all_variables = self.processor.get_available_variables(idx_url)
            
            if data_source in ['3DRTMA', 'RTMA-PRES']:
                # Filter out variables without proper short name mappings
                filtered_variables = []
                for var in all_variables:
                    if var in self.config.VARIABLE_INFO:
                        filtered_variables.append(var)
                return filtered_variables
            else:
                # For RTMA surface, return all variables
                return all_variables
                
        except Exception as e:
            logger.error(f"Error getting filtered variables: {e}")
            return []
    
    def get_variables_for_pressure_level(self, date: str, hour: int, data_source: str, pressure_level: int) -> List[str]:
        """Get available variables for a specific pressure level in pressure-enabled data sources."""
        source_config = self.config.DATA_SOURCES.get(data_source, {})
        if not source_config.get('has_pressure_levels', False):
            return self.get_filtered_variables(date, hour, data_source)
        
        try:
            grib_url, idx_url = self.generate_urls(date, hour, data_source)
            inventory = self.processor.get_grib_inventory(idx_url)
            
            # Find variables available at the specified pressure level
            available_variables = set()
            for record in inventory:
                level_str = record['level']
                variable = record['variable']
                
                # Check if this record matches the requested pressure level
                level_matches = False
                if pressure_level == 0:
                    # Surface level
                    if 'surface' in level_str.lower() or 'sfc' in level_str.lower():
                        level_matches = True
                else:
                    # Pressure level
                    if f"{pressure_level} mb" in level_str or f"{pressure_level}mb" in level_str:
                        level_matches = True
                
                if level_matches and variable in self.config.VARIABLE_INFO:
                    available_variables.add(variable)
            
            return sorted(list(available_variables))
            
        except Exception as e:
            logger.error(f"Error getting variables for pressure level {pressure_level}: {e}")
            return []
    
    def create_single_variable_weather_map(self, date: str, hour: int, output_path: str, variable_name: str = 'TMP', data_source: str = None, pressure_level: Optional[int] = None) -> bool:
        """Create weather map for a single variable (faster than loading all variables)."""
        try:
            level_msg = f" at {pressure_level}mb" if pressure_level else ""
            logger.info(f"Creating single variable weather map for {date} {hour:02d}Z, variable: {variable_name}{level_msg}, source: {data_source or 'RTMA'}")
            
            # Generate URLs
            grib_url, idx_url = self.generate_urls(date, hour, data_source)
            logger.info(f"GRIB URL: {grib_url}")
            logger.info(f"Index URL: {idx_url}")
            
            # Get available variables first
            available_variables = self.get_filtered_variables(date, hour, data_source or self.config.DEFAULT_DATA_SOURCE)
            if not available_variables:
                logger.error("No variables found in data")
                return False
            
            # Use first available variable if requested one not found
            if variable_name not in available_variables:
                logger.warning(f"Variable {variable_name} not found, using {available_variables[0]}")
                variable_name = available_variables[0]
            
            # Load single variable data
            variable_data, coords = self.processor.load_single_variable(grib_url, idx_url, variable_name, pressure_level)
            
            if not variable_data or coords is None:
                logger.error(f"Failed to load variable {variable_name}")
                return False
            
            # Create map
            weather_map = self.renderer.create_single_variable_map(
                variable_data, coords, variable_name, available_variables, date, hour, data_source or 'RTMA'
            )
            
            # Save map
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            weather_map.save(str(output_path))
            
            logger.info(f"Single variable weather map saved to: {output_path}")
            logger.info(f"Variable: {variable_data['info']['name']} ({variable_name})")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to create single variable weather map: {e}")
            return False
    
    def get_variable_data_json(self, date: str, hour: int, variable_name: str, data_source: str = None, pressure_level: Optional[int] = None) -> Dict[str, Any]:
        """Get variable data as JSON for AJAX requests."""
        try:
            level_msg = f" at {pressure_level}mb" if pressure_level else ""
            logger.info(f"get_variable_data_json called with date={date}, hour={hour}, variable={variable_name}{level_msg}, source={data_source or 'RTMA'}")
            
            # Validate date format
            if not date or len(date) != 8 or not date.isdigit():
                error_msg = f"Invalid date format: {date}. Expected YYYYMMDD format."
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}
            
            # Generate URLs
            grib_url, idx_url = self.generate_urls(date, hour, data_source)
            logger.info(f"Generated URLs - GRIB: {grib_url}, IDX: {idx_url}")
            
            # Load single variable data
            variable_data, coords = self.processor.load_single_variable(grib_url, idx_url, variable_name, pressure_level)
            
            if not variable_data or coords is None:
                error_msg = f'Failed to load variable {variable_name} for date {date} hour {hour}'
                level_msg = f" at {pressure_level}mb" if pressure_level and pressure_level > 0 else " at surface" if pressure_level == 0 else ""
                if level_msg:
                    error_msg += level_msg
                    
                # Only mention JPEG compression if it's actually a JPEG error, not for all 3DRTMA failures
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}
            
            # Create image overlay
            lat_grid = coords['lat_grid']
            lon_grid = coords['lon_grid']
            data = variable_data['data']
            var_info = variable_data['info']
            
            # Create contour levels
            vmin, vmax = float(data.min()), float(data.max())
            levels = np.linspace(vmin, vmax, self.config.CONTOUR_LEVELS)
            
            logger.info(f"Data range for {variable_name}: {vmin:.2f} to {vmax:.2f}")
            
            # Create contour overlay
            img_data = self.renderer.create_contour_overlay(
                lon_grid, lat_grid, data, levels=levels, cmap=var_info['cmap']
            )
            
            # Prepare bounds
            bounds = [[float(lat_grid.min()), float(lon_grid.min())], 
                      [float(lat_grid.max()), float(lon_grid.max())]]
            
            logger.info(f"Successfully created overlay for {variable_name}")
            
            return {
                'success': True,
                'image_data': img_data,
                'bounds': bounds,
                'variable_info': {
                    'name': var_info['name'],
                    'units': var_info['units'],
                    'min': vmin,
                    'max': vmax,
                    'cmap': var_info['cmap']
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to get variable data: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    def create_weather_map(self, date: str, hour: int, output_path: str, data_source: str = None) -> bool:
        """Create weather map for specified date and hour."""
        try:
            logger.info(f"Creating weather map for {date} {hour:02d}Z, source: {data_source or 'RTMA'}")
            
            # Generate URLs
            grib_url, idx_url = self.generate_urls(date, hour, data_source)
            logger.info(f"GRIB URL: {grib_url}")
            logger.info(f"Index URL: {idx_url}")
            
            # Load data
            all_data, coords = self.processor.load_all_variables(grib_url, idx_url)
            
            if not all_data or coords is None:
                logger.error("Failed to load any weather data")
                return False
            
            # Create map
            weather_map = self.renderer.create_multi_variable_map(all_data, coords)
            
            # Save map
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            weather_map.save(str(output_path))
            
            logger.info(f"Weather map saved to: {output_path}")
            logger.info(f"Successfully loaded {len(all_data)} variables")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to create weather map: {e}")
            return False


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate interactive weather maps from NOAA RTMA data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate map for today at 12Z
  python weather_map_generator.py --hour 12
  
  # Generate map for specific date
  python weather_map_generator.py --date 20250801 --hour 18 --output /path/to/map.html
  
  # Use verbose logging
  python weather_map_generator.py --date 20250801 --hour 12 --verbose
        """
    )
    
    # Get today's date as default
    today = datetime.now(timezone.utc).strftime('%Y%m%d')
    
    parser.add_argument(
        '--date', '-d',
        type=str,
        default=today,
        help=f'Date in YYYYMMDD format (default: {today})'
    )
    
    parser.add_argument(
        '--hour',
        type=int,
        default=12,
        choices=range(0, 24),
        help='Hour in UTC (0-23, default: 12)'
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        default='weather_map.html',
        help='Output HTML file path (default: weather_map.html)'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_arguments()
    
    # Configure logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Validate date format
    try:
        datetime.strptime(args.date, '%Y%m%d')
    except ValueError:
        logger.error(f"Invalid date format: {args.date}. Use YYYYMMDD format.")
        sys.exit(1)
    
    # Create weather map generator
    generator = WeatherMapGenerator()
    
    # Generate map
    success = generator.create_weather_map(args.date, args.hour, args.output)
    
    if success:
        logger.info("Weather map generation completed successfully!")
        sys.exit(0)
    else:
        logger.error("Weather map generation failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()