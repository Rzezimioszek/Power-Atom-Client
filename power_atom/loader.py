# -*- coding: utf-8 -*-
import os
import zipfile
import tempfile
import shutil
import logging
from qgis.core import QgsVectorLayer, QgsProject

logger = logging.getLogger("PowerAtom")

class DataLoader:
    """Handles loading datasets into QGIS from a local file."""

    def __init__(self, iface):
        self.iface = iface

    def load_local_file(self, file_path: str, title: str):
        """Processes a local file, adds it to QGIS, and deletes ZIPs after extraction."""
        try:
            if not os.path.exists(file_path):
                return

            if file_path.lower().endswith(".zip"):
                success = self._handle_zip(file_path, title)
                if success:
                    try:
                        os.remove(file_path)
                        logger.info(f"Deleted source ZIP file: {file_path}")
                    except Exception as e:
                        logger.warning(f"Could not delete ZIP file {file_path}: {e}")
            else:
                self._add_layer(file_path, title)
        except Exception as e:
            logger.error(f"Failed to load file {file_path}: {e}")
            if self.iface:
                self.iface.messageBar().pushMessage(
                    "Power Atom", f"Error loading file: {str(e)}", level=logging.ERROR
                )

    def _handle_zip(self, zip_path: str, title: str) -> bool:
        """Extracts ZIP to a permanent data folder and loads vector layers."""
        # We extract to a dedicated 'power_atom_data' folder in the user's temp dir
        # so files persist for the QGIS session, but the ZIP itself is removed.
        data_dir = os.path.join(tempfile.gettempdir(), "power_atom_data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
            
        extract_dir = tempfile.mkdtemp(prefix="ext_", dir=data_dir)
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)
            
            found_any = False
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Common vector formats
                    if file.lower().endswith((".gml", ".shp", ".json", ".geojson", ".sqlite", ".gpkg", ".tab", ".kml")):
                        self._add_layer(file_path, f"{title} ({file})")
                        found_any = True
            return found_any
        except Exception as e:
            logger.error(f"ZIP extraction failed: {e}")
            return False

    def _add_layer(self, file_path: str, title: str):
        """Adds a single file as a layer to QGIS."""
        layer = QgsVectorLayer(file_path, title, "ogr")
        if not layer.isValid():
            logger.error(f"Layer is not valid: {file_path}")
            return
        
        QgsProject.instance().addMapLayer(layer)
        if self.iface:
            self.iface.zoomToActiveLayer()
