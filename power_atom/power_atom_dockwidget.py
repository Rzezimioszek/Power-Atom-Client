# -*- coding: utf-8 -*-
import os
import logging
import re
import urllib.parse
from qgis.PyQt import QtGui, QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal, Qt, QThread, pyqtSlot, QSortFilterProxyModel, QSettings
from qgis.PyQt.QtGui import QStandardItemModel, QStandardItem, QIcon
from qgis.core import QgsRectangle, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

from .atom_client import AtomClient
from .atom_parser import AtomParser, AtomNode, AtomLink
from .loader import DataLoader

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'power_atom_dockwidget_base.ui'))

logger = logging.getLogger("PowerAtom")

class PackageItemWidget(QtWidgets.QWidget):
    downloadClicked = pyqtSignal(AtomLink)
    def __init__(self, link: AtomLink, parent=None):
        super().__init__(parent)
        self.link = link
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        title = link.title or os.path.basename(link.url)
        self.label = QtWidgets.QLabel(f"<b>{title}</b><br/><small>{link.mime_type or ''}</small>")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.btn = QtWidgets.QPushButton("Download & Load")
        self.btn.clicked.connect(lambda: self.downloadClicked.emit(self.link))
        layout.addWidget(self.btn)

class FetchTask(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.client = AtomClient()
    def run(self):
        try:
            content = self.client.fetch(self.url)
            nodes = AtomParser().parse(content, self.url)
            self.finished.emit(nodes)
        except Exception as e:
            self.error.emit(str(e))

class DownloadTask(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()
    def __init__(self, url: str, target_path: str, client: AtomClient):
        super().__init__()
        self.url = url
        self.target_path = target_path
        self.client = client
    def run(self):
        try:
            def progress_cb(cur, total):
                status = f"Downloading: {cur/(1024*1024):.1f} / {total/(1024*1024):.1f} MB"
                self.progress.emit(cur, total, status)
            self.client.download_to_file(self.url, self.target_path, progress_cb)
            self.finished.emit(self.target_path)
        except InterruptedError: self.cancelled.emit()
        except Exception as e: self.error.emit(str(e))

class PowerAtomDockWidget(QtWidgets.QDockWidget, FORM_CLASS):
    closingPlugin = pyqtSignal()

    def __init__(self, iface, parent=None):
        super(PowerAtomDockWidget, self).__init__(parent)
        self.iface = iface
        self.setupUi(self)
        self.settings = QSettings("PowerAtom", "PowerAtomClient")
        self.client = AtomClient()
        self.loader = DataLoader(self.iface)
        
        self.model = QStandardItemModel()
        self.proxy_model = QSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy_model.setRecursiveFilteringEnabled(True)
        self.treeView.setModel(self.proxy_model)
        
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)

        self.load_url_history()
        self.loadButton.clicked.connect(self.on_load_clicked)
        self.filterLineEdit.textChanged.connect(self.proxy_model.setFilterFixedString)
        self.treeView.selectionModel().selectionChanged.connect(self.on_tree_selection_changed)
        self.cancelButton.clicked.connect(self.on_cancel_download)
        self.zoomToBBoxButton.clicked.connect(self.on_zoom_to_bbox_clicked)

        self._current_bbox = None
        self._tasks = []

    def load_url_history(self):
        history = self.settings.value("url_history", [])
        self.urlComboBox.clear()
        if history: self.urlComboBox.addItems(history)

    def save_url_history(self, url):
        history = self.settings.value("url_history", [])
        if url in history: history.remove(url)
        history.insert(0, url)
        history = history[:10]
        self.settings.setValue("url_history", history)
        self.load_url_history()

    def on_tree_selection_changed(self, selected, deselected):
        indexes = self.treeView.selectionModel().selectedIndexes()
        if not indexes:
            self.detailsTextEdit.clear()
            self.packageListWidget.clear()
            self.zoomToBBoxButton.setEnabled(False)
            self._current_bbox = None
            return
        proxy_index = indexes[0]
        source_index = self.proxy_model.mapToSource(proxy_index)
        item = self.model.itemFromIndex(source_index)
        node = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(node, AtomNode):
            self.display_node_details(node)
            self.auto_fetch_packages(node)

    def display_node_details(self, node):
        details = [f"<b>Title:</b> {node.title}"]
        if node.updated: details.append(f"<b>Updated:</b> {node.updated}")
        if node.rights: details.append(f"<b>Rights:</b> {node.rights}")
        if node.bbox:
            details.append(f"<b>BBox:</b> {node.bbox}")
            self._current_bbox = node.bbox
            self.zoomToBBoxButton.setEnabled(True)
        else:
            self._current_bbox = None
            self.zoomToBBoxButton.setEnabled(False)
        if node.summary: details.append(f"<br/><b>Summary:</b><br/>{node.summary}")
        self.detailsTextEdit.setHtml("<br/>".join(details))

    @pyqtSlot()
    def on_zoom_to_bbox_clicked(self):
        if not self._current_bbox: return
        try:
            rect = QgsRectangle(self._current_bbox[0], self._current_bbox[1], self._current_bbox[2], self._current_bbox[3])
            crs_src = QgsCoordinateReferenceSystem("EPSG:4326")
            crs_dest = self.iface.mapCanvas().mapSettings().destinationCrs()
            if crs_src != crs_dest:
                xform = QgsCoordinateTransform(crs_src, crs_dest, QgsProject.instance())
                rect = xform.transformBoundingBox(rect)
            self.iface.mapCanvas().setExtent(rect)
            self.iface.mapCanvas().refresh()
        except Exception as e: logger.error(f"Zoom to BBox failed: {e}")

    def auto_fetch_packages(self, node):
        self.packageListWidget.clear()
        self.statusLabel.setText("Fetching packages...")
        
        # Add a "Searching..." placeholder
        self.packageListWidget.addItem("Searching for packages...")

        packages = [l for l in node.links if self._is_package_link(l)]
        if packages:
            self.packageListWidget.clear()
            for pkg in packages: self.add_package_to_list(pkg)
        
        alternates = [l for l in node.links if self._is_atom_link(l)]
        for alt in alternates:
            self.fetch_packages_from_url(alt.url, depth=0)
        
        if not packages and not alternates:
            self.packageListWidget.clear()
            self.statusLabel.setText("No packages found.")
        else:
            self.statusLabel.setText("Ready")

    def fetch_packages_from_url(self, url, depth=0):
        if depth > 3: return
        task = FetchTask(url)
        self._tasks.append(task)
        task.finished.connect(lambda nodes: self.populate_packages_from_subfeed(nodes, task, depth))
        task.error.connect(lambda err: self.on_error(err, task))
        task.start()

    def populate_packages_from_subfeed(self, nodes, task, depth):
        new_packages_found = False
        for node in nodes:
            found_package_in_entry = False
            for link in node.links:
                if self._is_package_link(link):
                    if not link.title: link.title = node.title
                    self.add_package_to_list(link)
                    found_package_in_entry = True
                    new_packages_found = True
            
            if not found_package_in_entry:
                for link in node.links:
                    if self._is_atom_link(link):
                        self.fetch_packages_from_url(link.url, depth + 1)

        if task in self._tasks: self._tasks.remove(task)
        
        # Remove placeholder if we found something
        if self.packageListWidget.count() > 0:
            first_item = self.packageListWidget.item(0)
            if first_item and first_item.text() == "Searching for packages...":
                self.packageListWidget.takeItem(0)

        self.statusLabel.setText("Ready")

    @pyqtSlot()
    def on_load_clicked(self):
        url = self.urlComboBox.currentText().strip()
        if not url: return
        self.save_url_history(url)
        self.statusLabel.setText("Fetching...")
        self.model.clear()
        self.packageListWidget.clear()
        self.detailsTextEdit.clear()
        self.start_fetch(url, self.model.invisibleRootItem())

    def start_fetch(self, url, parent_item):
        task = FetchTask(url)
        self._tasks.append(task)
        task.finished.connect(lambda nodes: self.populate_tree(nodes, parent_item, task))
        task.error.connect(lambda err: self.on_error(err, task))
        task.start()

    def populate_tree(self, nodes, parent_item, task):
        for node in nodes:
            item = QStandardItem(node.title)
            item.setData(node, Qt.ItemDataRole.UserRole)
            parent_item.appendRow(item)
        if task in self._tasks: self._tasks.remove(task)
        self.statusLabel.setText("Ready")

    def _is_atom_link(self, link: AtomLink) -> bool:
        """Determines if a link points to another ATOM feed."""
        mimetype = (link.mime_type or "").lower()
        if "atom+xml" in mimetype:
            return True
        
        url_lower = link.url.lower()
        # If it's a known data format, it's NOT an atom link
        if any(ext in url_lower for ext in [".zip", ".gml", ".shp", ".json", ".geojson", ".gpkg", "download.php", "getfile"]):
            return False
            
        if link.rel in ["self", "alternate"]:
            if "atom" in url_lower or url_lower.endswith(".xml") or "index.php" in url_lower:
                return True
        return False

    def _is_package_link(self, link: AtomLink) -> bool:
        """Determines if a link points to a data package."""
        mimetype = (link.mime_type or "").lower()
        url_lower = link.url.lower()
        
        # Enclosures are almost always packages
        if link.rel == "enclosure":
            return True
            
        # Check mimetype
        data_mimetypes = ["zip", "gml", "json", "shp", "gpkg", "sqlite", "octet-stream"]
        if any(t in mimetype for t in data_mimetypes):
            return True
            
        # Check URL extensions or keywords
        data_keywords = [".zip", ".gml", ".json", ".geojson", ".shp", ".gpkg", ".sqlite", "download.php", "getfile"]
        if any(kw in url_lower for kw in data_keywords):
            return True
            
        return False

    def add_package_to_list(self, link):
        # Remove "Searching..." placeholder if present
        if self.packageListWidget.count() == 1:
            first_item = self.packageListWidget.item(0)
            if first_item and first_item.text() == "Searching for packages...":
                self.packageListWidget.takeItem(0)

        for i in range(self.packageListWidget.count()):
            item = self.packageListWidget.item(i)
            widget = self.packageListWidget.itemWidget(item)
            if widget and widget.link.url == link.url: return
            
        list_item = QtWidgets.QListWidgetItem(self.packageListWidget)
        widget = PackageItemWidget(link)
        widget.downloadClicked.connect(self.on_download_package)
        list_item.setSizeHint(widget.sizeHint())
        self.packageListWidget.addItem(list_item)
        self.packageListWidget.setItemWidget(list_item, widget)

    def _sanitize_filename(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        filename = params['name'][0] if 'name' in params else os.path.basename(parsed.path)
        if not filename: filename = "dataset"
        filename = re.sub(r'[<>:"/\\|?*&%=]', '_', filename)
        return re.sub(r'_+', '_', filename)

    def on_download_package(self, link: AtomLink):
        default_name = self._sanitize_filename(link.url)
        valid_exts = [".zip", ".gml", ".json", ".geojson", ".shp", ".gpkg", ".sqlite", ".tab", ".kml"]
        if not any(default_name.lower().endswith(ext) for ext in valid_exts):
            if "zip" in link.mime_type: default_name += ".zip"
            elif "gml" in link.mime_type: default_name += ".gml"
            elif "json" in link.mime_type: default_name += ".geojson"
            else: default_name += ".xml"

        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Dataset", default_name, "All Files (*.*)")
        if not file_path: return

        self.progressBar.setVisible(True)
        self.cancelButton.setVisible(True)
        self.progressBar.setValue(0)
        self.dl_task = DownloadTask(link.url, file_path, self.client)
        self.dl_task.progress.connect(self.on_download_progress)
        self.dl_task.finished.connect(lambda path: self.on_download_finished(path, link.title))
        self.dl_task.error.connect(self.on_error)
        self.dl_task.cancelled.connect(self.on_download_cancelled)
        self.dl_task.start()

    def on_download_progress(self, cur, total, status_text):
        if total > 0: self.progressBar.setValue(int(cur / total * 100))
        self.statusLabel.setText(status_text)

    def on_download_finished(self, path, title):
        self.progressBar.setVisible(False)
        self.cancelButton.setVisible(False)
        self.loader.load_local_file(path, title or "Data")
        self.statusLabel.setText("Ready")

    def on_download_cancelled(self):
        self.progressBar.setVisible(False)
        self.cancelButton.setVisible(False)
        self.statusLabel.setText("Download cancelled.")

    @pyqtSlot()
    def on_cancel_download(self):
        self.client.cancel()
        self.statusLabel.setText("Cancelling...")

    def on_error(self, err, task=None):
        self.progressBar.setVisible(False)
        self.cancelButton.setVisible(False)
        self.statusLabel.setText(f"Error: {err}")
        logger.error(f"Error: {err}")

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()
