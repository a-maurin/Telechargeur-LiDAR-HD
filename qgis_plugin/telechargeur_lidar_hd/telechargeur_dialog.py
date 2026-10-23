"""
telechargeur_dialog.py
Fenetre de dialogue graphique PyQt pour l'extension Telechargeur LiDAR HD.
"""

import os
from osgeo import ogr, osr

try:
    from qgis.PyQt.QtCore import Qt, pyqtSignal
    from qgis.PyQt.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QSpinBox,
        QVBoxLayout,
    )
    from qgis.core import (
        QgsApplication,
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsGeometry,
        QgsMapLayer,
        QgsProject,
        QgsRectangle,
        QgsVectorLayer,
        QgsWkbTypes,
    )
    HAS_QGIS = True
except ImportError:
    HAS_QGIS = False
    from PyQt5.QtCore import Qt, pyqtSignal
    from PyQt5.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QSpinBox,
        QVBoxLayout,
    )

from .telechargeur_task import LidarDownloaderTask

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
INTERNAL_SOUS_BASSINS_GPKG = os.path.join(PLUGIN_DIR, "data", "sous_bassins_armancon.gpkg")


class TelechargeurDialog(QDialog):
    """Boite de dialogue principale pour parametrer et lancer le telechargement."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.current_task = None
        self.calculated_tiles = []
        self._tiles_cache = {}

        self.setWindowTitle("Téléchargeur LiDAR HD (MNT 0m50)")
        self.resize(600, 540)
        self.setup_ui()
        self.populate_layers()
        self.populate_sub_basins()

        # Connexions automatiques pour le précalcul dynamique
        self.combo_layers.currentIndexChanged.connect(self.calculate_tiles)
        self.chk_selected_only.toggled.connect(self.calculate_tiles)
        self.combo_sub_basins.currentIndexChanged.connect(self.calculate_tiles)
        self.chk_compress.toggled.connect(self.update_volume_label)

        # Premier calcul automatique à l'ouverture
        self.calculate_tiles()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)

        # Groupe 1 : Choix de l'emprise
        group_emprise = QGroupBox("1. Définition de l'emprise géographique", self)
        layout_emprise = QVBoxLayout(group_emprise)

        self.radio_layer = QRadioButton("Couche vectorielle du projet", self)
        self.radio_layer.setChecked(True)
        self.radio_layer.toggled.connect(self.on_mode_toggled)
        layout_emprise.addWidget(self.radio_layer)

        layer_sublayout = QHBoxLayout()
        self.combo_layers = QComboBox(self)
        layer_sublayout.addWidget(self.combo_layers, stretch=1)
        self.chk_selected_only = QCheckBox("Entités sélectionnées uniquement", self)
        layer_sublayout.addWidget(self.chk_selected_only)
        layout_emprise.addLayout(layer_sublayout)

        self.radio_sub_basin = QRadioButton("Sous-bassin hydrographique de l'Armançon", self)
        self.radio_sub_basin.toggled.connect(self.on_mode_toggled)
        layout_emprise.addWidget(self.radio_sub_basin)

        self.combo_sub_basins = QComboBox(self)
        self.combo_sub_basins.setEnabled(False)
        layout_emprise.addWidget(self.combo_sub_basins)

        main_layout.addWidget(group_emprise)

        # Groupe 2 : Dossier de destination
        group_dest = QGroupBox("2. Dossier de destination", self)
        layout_dest = QHBoxLayout(group_dest)
        self.edit_dest = QLineEdit(self)
        self.edit_dest.setText(os.path.expanduser("~/lidar_hd_mnt"))
        layout_dest.addWidget(self.edit_dest, stretch=1)
        self.btn_browse = QPushButton("Parcourir...", self)
        self.btn_browse.clicked.connect(self.browse_dest)
        layout_dest.addWidget(self.btn_browse)
        main_layout.addWidget(group_dest)

        # Groupe 3 : Options de telechargement
        group_options = QGroupBox("3. Options de traitement", self)
        layout_options = QFormLayout(group_options)

        self.chk_compress = QCheckBox("Compresser en GeoTIFF sans perte (économie ~50 %)", self)
        self.chk_compress.setChecked(True)
        layout_options.addRow(self.chk_compress)

        self.chk_load_canvas = QCheckBox("Charger automatiquement les couches dans la carte QGIS", self)
        self.chk_load_canvas.setChecked(True)
        layout_options.addRow(self.chk_load_canvas)

        layout_vrt = QHBoxLayout()
        self.chk_create_vrt = QCheckBox("Assembler en raster virtuel (VRT) avec ombrage", self)
        self.chk_create_vrt.setChecked(True)
        self.edit_vrt_name = QLineEdit("mosaique_mnt.vrt", self)
        self.chk_create_vrt.toggled.connect(self.edit_vrt_name.setEnabled)
        layout_vrt.addWidget(self.chk_create_vrt, stretch=1)
        layout_vrt.addWidget(self.edit_vrt_name)
        layout_options.addRow(layout_vrt)

        layout_gpkg = QHBoxLayout()
        self.chk_create_gpkg = QCheckBox("Créer le tableau d'assemblage vectoriel (GPKG)", self)
        self.chk_create_gpkg.setChecked(True)
        self.edit_gpkg_name = QLineEdit("tableau_assemblage_dalles.gpkg", self)
        self.chk_create_gpkg.toggled.connect(self.edit_gpkg_name.setEnabled)
        layout_gpkg.addWidget(self.chk_create_gpkg, stretch=1)
        layout_gpkg.addWidget(self.edit_gpkg_name)
        layout_options.addRow(layout_gpkg)

        self.spin_threads = QSpinBox(self)
        self.spin_threads.setRange(1, 5)
        self.spin_threads.setValue(3)
        layout_options.addRow("Téléchargements simultanés :", self.spin_threads)

        main_layout.addWidget(group_options)

        # Groupe 4 : Progression et informations
        group_progress = QGroupBox("Progression", self)
        layout_progress = QVBoxLayout(group_progress)
        self.lbl_info = QLabel("Sélectionnez votre emprise pour calculer les dalles.", self)
        layout_progress.addWidget(self.lbl_info)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout_progress.addWidget(self.progress_bar)
        main_layout.addWidget(group_progress)

        # Boutons d'action
        layout_buttons = QHBoxLayout()
        self.btn_calc = QPushButton("Calculer les dalles", self)
        self.btn_calc.clicked.connect(self.calculate_tiles)
        layout_buttons.addWidget(self.btn_calc)

        self.btn_start = QPushButton("Démarrer le téléchargement", self)
        self.btn_start.setObjectName("btn_start")
        self.btn_start.setStyleSheet(
            "QPushButton#btn_start:enabled { font-weight: bold; color: #2e7d32; border: 1px solid #2e7d32; border-radius: 3px; padding: 4px 10px; }"
            "QPushButton#btn_start:disabled { font-weight: normal; color: #888888; background-color: #f0f0f0; border: 1px solid #d0d0d0; border-radius: 3px; padding: 4px 10px; }"
        )
        self.btn_start.clicked.connect(self.start_download)
        layout_buttons.addWidget(self.btn_start)

        self.btn_cancel = QPushButton("Annuler", self)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_download)
        layout_buttons.addWidget(self.btn_cancel)

        self.btn_close = QPushButton("Fermer", self)
        self.btn_close.clicked.connect(self.close)
        layout_buttons.addWidget(self.btn_close)

        main_layout.addLayout(layout_buttons)

    def on_mode_toggled(self):
        is_layer = self.radio_layer.isChecked()
        self.combo_layers.setEnabled(is_layer)
        self.chk_selected_only.setEnabled(is_layer)
        self.combo_sub_basins.setEnabled(not is_layer)
        self.calculate_tiles()

    def browse_dest(self):
        folder = QFileDialog.getExistingDirectory(self, "Choisir le dossier de destination", self.edit_dest.text())
        if folder:
            self.edit_dest.setText(folder)

    def populate_layers(self):
        self.combo_layers.clear()
        if not HAS_QGIS:
            self.combo_layers.addItem("Mode autonome (pas de projet QGIS)")
            return

        project = QgsProject.instance()
        for layer_id, layer in project.mapLayers().items():
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PolygonGeometry:
                self.combo_layers.addItem(layer.name(), layer_id)

        if self.combo_layers.count() == 0:
            self.combo_layers.addItem("(Aucune couche polygone dans le projet)")
            self.radio_sub_basin.setChecked(True)

    def populate_sub_basins(self):
        self.combo_sub_basins.clear()
        self.combo_sub_basins.addItem("Bassin versant complet de l'Armançon (3 323 dalles)", "ALL")
        if os.path.exists(INTERNAL_SOUS_BASSINS_GPKG):
            try:
                ds = ogr.Open(INTERNAL_SOUS_BASSINS_GPKG)
                layer = ds.GetLayer()
                for feat in layer:
                    nom = feat.GetField("sous_bassin")
                    surf = feat.GetField("surface_km2")
                    if nom:
                        self.combo_sub_basins.addItem(f"{nom} ({surf:.1f} km²)", nom)
                ds = None
            except Exception:
                pass

    def extract_geometry_from_layer(self, layer_id, selected_only=False):
        """Extrait la geometrie unifiee en Lambert 93 depuis une couche QGIS."""
        project = QgsProject.instance()
        layer = project.mapLayer(layer_id)
        if not layer or not layer.isValid():
            return None

        crs_2154 = QgsCoordinateReferenceSystem("EPSG:2154")
        transform = QgsCoordinateTransform(layer.crs(), crs_2154, project)

        features = layer.selectedFeatures() if selected_only and layer.selectedFeatureCount() > 0 else layer.getFeatures()
        combined_geom = QgsGeometry()

        for feat in features:
            g = feat.geometry()
            if g and not g.isEmpty():
                g_clone = QgsGeometry(g)
                g_clone.transform(transform)
                if combined_geom.isEmpty():
                    combined_geom = g_clone
                else:
                    combined_geom = combined_geom.combine(g_clone)

        return combined_geom

    def calculate_tiles(self):
        """Calcule la liste des dalles 1km x 1km a telecharger."""
        tiles = []
        if self.radio_layer.isChecked() and HAS_QGIS:
            layer_id = self.combo_layers.currentData()
            if not layer_id:
                QMessageBox.warning(self, "Attention", "Veuillez sélectionner une couche vectorielle valide.")
                return []
            geom = self.extract_geometry_from_layer(layer_id, self.chk_selected_only.isChecked())
            if not geom or geom.isEmpty():
                QMessageBox.warning(self, "Attention", "Aucune géométrie exploitable dans la couche sélectionnée.")
                return []

            # Calcul des dalles via boite englobante
            bbox = geom.boundingBox()
            x_start = int(bbox.xMinimum() // 1000)
            x_end = int(bbox.xMaximum() // 1000) + 1
            y_start = int(bbox.yMinimum() // 1000) + 1
            y_end = int(bbox.yMaximum() // 1000) + 2

            for x in range(x_start, x_end):
                for y in range(y_start, y_end):
                    tile_box = QgsGeometry.fromRect(
                        QgsRectangle(x * 1000.0, (y - 1) * 1000.0, (x + 1) * 1000.0, y * 1000.0)
                    )
                    if geom.intersects(tile_box):
                        tiles.append((x, y))

        else:
            # Mode sous-bassin integre
            target_sub = self.combo_sub_basins.currentData()
            if target_sub in self._tiles_cache:
                tiles = self._tiles_cache[target_sub]
                self.calculated_tiles = tiles
                self.progress_bar.setRange(0, len(tiles) if len(tiles) > 0 else 1)
                self.progress_bar.setValue(0)
                self.progress_bar.setFormat("%v / %m dalles (%p%)")
                self.update_volume_label()
                return tiles

            if not os.path.exists(INTERNAL_SOUS_BASSINS_GPKG):
                QMessageBox.critical(self, "Erreur", "Fichier interne des sous-bassins introuvable.")
                return []

            ds = ogr.Open(INTERNAL_SOUS_BASSINS_GPKG)
            layer = ds.GetLayer()
            tiles_set = set()

            for feat in layer:
                nom = feat.GetField("sous_bassin")
                if target_sub == "ALL" or (nom and target_sub and target_sub.lower() in nom.lower()):
                    geom = feat.GetGeometryRef()
                    if geom and not geom.IsEmpty():
                        env = geom.GetEnvelope()
                        x_start = int(env[0] // 1000)
                        x_end = int(env[1] // 1000) + 1
                        y_start = int(env[2] // 1000) + 1
                        y_end = int(env[3] // 1000) + 2

                        for x in range(x_start, x_end):
                            for y in range(y_start, y_end):
                                tile_poly = ogr.Geometry(ogr.wkbPolygon)
                                ring = ogr.Geometry(ogr.wkbLinearRing)
                                ring.AddPoint(x * 1000.0, (y - 1) * 1000.0)
                                ring.AddPoint((x + 1) * 1000.0, (y - 1) * 1000.0)
                                ring.AddPoint((x + 1) * 1000.0, y * 1000.0)
                                ring.AddPoint(x * 1000.0, y * 1000.0)
                                ring.AddPoint(x * 1000.0, (y - 1) * 1000.0)
                                tile_poly.AddGeometry(ring)

                                if geom.Intersects(tile_poly):
                                    tiles_set.add((x, y))

            ds = None
            tiles = sorted(list(tiles_set))
            if target_sub:
                self._tiles_cache[target_sub] = tiles

        self.calculated_tiles = tiles
        self.progress_bar.setRange(0, len(tiles) if len(tiles) > 0 else 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v / %m dalles (%p%)")
        self.update_volume_label()
        return tiles

    def update_volume_label(self):
        tiles = self.calculated_tiles
        vol_brut = len(tiles) * 15.3 / 1024.0
        vol_comp = len(tiles) * 7.0 / 1024.0
        vol_estime = vol_comp if self.chk_compress.isChecked() else vol_brut
        comp_str = "compressé" if self.chk_compress.isChecked() else "brut"
        self.lbl_info.setText(
            f"<b>{len(tiles)} dalles identifiées</b> (volume estimé : ~{vol_estime:.2f} Go {comp_str})"
        )

    def start_download(self):
        # Toujours forcer le recalcul exact selon la sélection courante au moment du clic
        tiles = self.calculate_tiles()
        if not tiles:
            return

        dest_dir = self.edit_dest.text().strip()
        if not dest_dir:
            QMessageBox.warning(self, "Attention", "Veuillez indiquer un dossier de destination.")
            return

        os.makedirs(dest_dir, exist_ok=True)

        # Verification de l'existence prealable des fichiers VRT et GPKG
        create_vrt = self.chk_create_vrt.isChecked()
        vrt_name = self.edit_vrt_name.text().strip() or "mosaique_mnt.vrt"
        if not vrt_name.lower().endswith(".vrt"):
            vrt_name += ".vrt"

        create_gpkg = self.chk_create_gpkg.isChecked()
        gpkg_name = self.edit_gpkg_name.text().strip() or "tableau_assemblage_dalles.gpkg"
        if not gpkg_name.lower().endswith(".gpkg"):
            gpkg_name += ".gpkg"

        if create_vrt and os.path.exists(os.path.join(dest_dir, vrt_name)):
            rep = QMessageBox.question(
                self,
                "Fichier existant",
                f"Le fichier raster virtuel '{vrt_name}' existe déjà dans le dossier cible.\nSouhaitez-vous le remplacer ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if rep == QMessageBox.No:
                create_vrt = False

        if create_gpkg and os.path.exists(os.path.join(dest_dir, gpkg_name)):
            rep = QMessageBox.question(
                self,
                "Fichier existant",
                f"Le tableau d'assemblage '{gpkg_name}' existe déjà dans le dossier cible.\nSouhaitez-vous le remplacer ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if rep == QMessageBox.No:
                create_gpkg = False

        self.btn_start.setEnabled(False)
        self.btn_calc.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setRange(0, len(tiles))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v / %m dalles (%p%)")

        # Creation de la tache de fond
        self.current_task = LidarDownloaderTask(
            tiles_list=tiles,
            output_dir=dest_dir,
            compress=self.chk_compress.isChecked(),
            load_in_canvas=self.chk_load_canvas.isChecked(),
            threads=self.spin_threads.value(),
            create_vrt=create_vrt,
            vrt_filename=vrt_name,
            create_gpkg=create_gpkg,
            gpkg_filename=gpkg_name,
        )
        self.current_task.bridge.tile_progress.connect(self.on_tile_progress)
        self.current_task.bridge.task_finished.connect(self.on_download_complete)

        if HAS_QGIS:
            QgsApplication.taskManager().addTask(self.current_task)
        else:
            self.current_task.run()
            self.current_task.finished(True)

    def on_tile_progress(self, current, total, filename, status, fsize):
        self.progress_bar.setValue(current)
        self.lbl_info.setText(f"[{current}/{total}] {filename} -> {status}")

    def on_download_complete(self, success, ok_count, skip_count, empty_count, err_count):
        self.btn_start.setEnabled(True)
        self.btn_calc.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if self.calculated_tiles:
            self.progress_bar.setValue(len(self.calculated_tiles))

        status_str = "achevé avec succès" if success else "interrompu"
        self.lbl_info.setText(f"Traitement {status_str}.")
        msg = (
            f"Traitement {status_str} !\n\n"
            f"Dalles téléchargées : {ok_count}\n"
            f"Dalles déjà présentes : {skip_count}\n"
            f"Dalles hors couverture (vides) : {empty_count}\n"
            f"Échecs : {err_count}"
        )
        QMessageBox.information(self, "Bilan du téléchargement", msg)

    def cancel_download(self):
        if self.current_task:
            self.current_task.cancel()
            self.lbl_info.setText("Annulation en cours...")
            self.btn_cancel.setEnabled(False)
