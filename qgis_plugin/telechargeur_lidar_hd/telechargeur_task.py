"""
telechargeur_task.py
Tache de fond QgsTask pour le telechargement asynchrone des dalles MNT LiDAR HD.
"""

import concurrent.futures
import glob
import os
import sys
import time
import urllib.error
import urllib.request
from osgeo import gdal, ogr, osr

try:
    from qgis.PyQt.QtCore import QObject, pyqtSignal
    from qgis.PyQt.QtGui import QColor, QFont
    from qgis.core import (
        Qgis,
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsFillSymbol,
        QgsGeometry,
        QgsHillshadeRenderer,
        QgsMessageLog,
        QgsPalLayerSettings,
        QgsProject,
        QgsRasterLayer,
        QgsSingleSymbolRenderer,
        QgsTask,
        QgsTextBufferSettings,
        QgsTextFormat,
        QgsVectorLayer,
        QgsVectorLayerSimpleLabeling,
    )
    HAS_QGIS = True
except ImportError:
    HAS_QGIS = False
    from PyQt5.QtCore import QObject, pyqtSignal
    # Classe de substitution si execute hors QGIS
    class QgsTask:
        CanCancel = 1

        def __init__(self, description="", flags=0):
            self.description = description
            self._canceled = False
            self._progress = 0.0

        def setProgress(self, progress):
            self._progress = progress

        def isCanceled(self):
            return self._canceled

        def cancel(self):
            self._canceled = True


class TaskBridge(QObject):
    """Pont de communication thread-safe vers l'interface graphique."""
    tile_progress = pyqtSignal(int, int, str, str, int)
    task_finished = pyqtSignal(bool, int, int, int, int)


WMS_BASE_URL = "https://data.geopf.fr/wms-r"
LAYER_NAME = "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"


def tile_coords_to_bbox(x_km, y_km):
    """Calcule l'emprise exacte au demi-pixel pres en Lambert-93 (EPSG:2154)."""
    xmin = x_km * 1000.0 - 0.25
    xmax = (x_km + 1) * 1000.0 - 0.25
    ymin = (y_km - 1) * 1000.0 + 0.25
    ymax = y_km * 1000.0 + 0.25
    return xmin, ymin, xmax, ymax


def build_wms_url(x_km, y_km, filename):
    """Construit la requete WMS GetMap officielle."""
    xmin, ymin, xmax, ymax = tile_coords_to_bbox(x_km, y_km)
    bbox_str = f"{xmin:.2f},{ymin:.2f},{xmax:.2f},{ymax:.2f}"
    params = [
        ("SERVICE", "WMS"),
        ("VERSION", "1.3.0"),
        ("EXCEPTIONS", "text/xml"),
        ("REQUEST", "GetMap"),
        ("LAYERS", LAYER_NAME),
        ("FORMAT", "image/geotiff"),
        ("STYLES", ""),
        ("CRS", "EPSG:2154"),
        ("BBOX", bbox_str),
        ("WIDTH", "2000"),
        ("HEIGHT", "2000"),
        ("FILENAME", filename),
    ]
    query = "&".join(f"{k}={v}" for k, v in params)
    return f"{WMS_BASE_URL}?{query}"


def verify_geotiff(filepath, reject_empty=True):
    """Verifie qu'un fichier est un GeoTIFF integre de 2000x2000 pixels."""
    ds = None
    try:
        ds = gdal.Open(filepath, gdal.GA_ReadOnly)
        if ds is None:
            return False, False
        if ds.RasterXSize != 2000 or ds.RasterYSize != 2000:
            return False, False

        band = ds.GetRasterBand(1)
        min_val, max_val = band.ComputeRasterMinMax(False)

        if reject_empty:
            nodata = band.GetNoDataValue()
            if (nodata is not None and min_val == nodata and max_val == nodata) or (
                min_val <= -9999.0 and max_val <= -9999.0
            ):
                return False, True

        return True, False
    except Exception:
        return False, False
    finally:
        ds = None


def generate_aux_xml(filepath):
    """Calcule les statistiques et genere le fichier .aux.xml."""
    ds = None
    try:
        ds = gdal.Open(filepath, gdal.GA_ReadOnly)
        if ds:
            band = ds.GetRasterBand(1)
            band.GetStatistics(False, True)
    except Exception:
        pass
    finally:
        ds = None


def download_single_tile(
    x_km,
    y_km,
    output_dir,
    compress=True,
    compute_stats=True,
    keep_empty=False,
    max_retries=3,
):
    """Telecharge une dalle avec reprise et controle d'integrite."""
    filename = f"LHD_FXX_{x_km:04d}_{y_km:04d}_MNT_O_0M50_LAMB93_IGN69.tif"
    final_path = os.path.join(output_dir, filename)
    aux_path = final_path + ".aux.xml"

    if os.path.exists(final_path) and os.path.getsize(final_path) > 10240:
        is_valid, _ = verify_geotiff(final_path, reject_empty=not keep_empty)
        if is_valid:
            if compute_stats and not os.path.exists(aux_path):
                generate_aux_xml(final_path)
            return filename, final_path, "deja present", os.path.getsize(final_path)

    url = build_wms_url(x_km, y_km, filename)
    tmp_path = os.path.join(output_dir, f".{filename}.{os.getpid()}.tmp")
    headers = {"User-Agent": "Mozilla/5.0 (QGIS LiDAR HD Downloader)"}

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if "xml" in content_type or "html" in content_type:
                    err_msg = response.read().decode("utf-8", errors="ignore")
                    raise RuntimeError(f"Reponse d'erreur du serveur : {err_msg[:200]}")

                with open(tmp_path, "wb") as f_out:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        f_out.write(chunk)

            is_valid, is_empty = verify_geotiff(tmp_path, reject_empty=not keep_empty)
            if is_empty:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                return filename, None, "hors couverture (NoData)", 0

            if not is_valid:
                raise RuntimeError("Fichier GeoTIFF corrompu ou incomplet.")

            if compress:
                opts = gdal.TranslateOptions(
                    creationOptions=[
                        "COMPRESS=DEFLATE",
                        "PREDICTOR=3",
                        "ZLEVEL=6",
                        "TILED=YES",
                    ]
                )
                ds_out = gdal.Translate(final_path, tmp_path, options=opts)
                ds_out = None
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            else:
                os.replace(tmp_path, final_path)

            if compute_stats:
                generate_aux_xml(final_path)

            return filename, final_path, "telecharge", os.path.getsize(final_path)

        except Exception as err:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            if attempt == max_retries:
                return filename, None, f"echec ({err})", 0
            time.sleep(2 * attempt)

    return filename, None, "echec (inconnu)", 0


def build_vrt(tif_list, output_vrt_path):
    """Construit un raster virtuel VRT a partir de la liste des dalles."""
    if not tif_list:
        return None
    try:
        ds = gdal.BuildVRT(output_vrt_path, tif_list)
        if ds is not None:
            ds = None
            return output_vrt_path
    except Exception as err:
        print(f"Erreur creation VRT : {err}", file=sys.stderr)
    return None


def create_assemblage_gpkg(tif_list, output_gpkg_path):
    """Cree une couche vectorielle GeoPackage representant le carroyage des dalles."""
    if not tif_list:
        return None

    try:
        if os.path.exists(output_gpkg_path):
            try:
                os.remove(output_gpkg_path)
            except OSError:
                pass

        driver = ogr.GetDriverByName("GPKG")
        ds = driver.CreateDataSource(output_gpkg_path)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(2154)

        layer = ds.CreateLayer("tableau_assemblage", srs, ogr.wkbPolygon)
        layer.CreateField(ogr.FieldDefn("nom_dalle", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("identifiant", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("chemin_fichier", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("taille_mo", ogr.OFTReal))

        for tif_path in tif_list:
            bname = os.path.basename(tif_path)
            parts = bname.replace(".tif", "").split("_")
            x_km, y_km = None, None
            for i in range(len(parts) - 1):
                if parts[i].isdigit() and parts[i + 1].isdigit() and len(parts[i]) == 4 and len(parts[i + 1]) == 4:
                    x_km = int(parts[i])
                    y_km = int(parts[i + 1])
                    break

            if x_km is None or y_km is None:
                continue

            ident = f"{x_km:04d}_{y_km:04d}"
            try:
                size_mo = round(os.path.getsize(tif_path) / (1024 * 1024), 2)
            except OSError:
                size_mo = 0.0

            poly = ogr.Geometry(ogr.wkbPolygon)
            ring = ogr.Geometry(ogr.wkbLinearRing)
            ring.AddPoint(x_km * 1000.0, (y_km - 1) * 1000.0)
            ring.AddPoint((x_km + 1) * 1000.0, (y_km - 1) * 1000.0)
            ring.AddPoint((x_km + 1) * 1000.0, y_km * 1000.0)
            ring.AddPoint(x_km * 1000.0, y_km * 1000.0)
            ring.AddPoint(x_km * 1000.0, (y_km - 1) * 1000.0)
            poly.AddGeometry(ring)

            feat = ogr.Feature(layer.GetLayerDefn())
            feat.SetField("nom_dalle", bname)
            feat.SetField("identifiant", ident)
            feat.SetField("chemin_fichier", os.path.abspath(tif_path))
            feat.SetField("taille_mo", size_mo)
            feat.SetGeometry(poly)
            layer.CreateFeature(feat)

        ds = None
        return output_gpkg_path
    except Exception as err:
        print(f"Erreur creation tableau assemblage GPKG : {err}", file=sys.stderr)
        return None


def apply_hillshade_to_raster(raster_layer):
    """Applique le rendu ombrage naturel standard (315 degres, 45 degres, Z=1)."""
    if not HAS_QGIS or not raster_layer or not raster_layer.isValid():
        return
    try:
        provider = raster_layer.dataProvider()
        renderer = QgsHillshadeRenderer(provider, 1, 315.0, 45.0)
        renderer.setZFactor(1.0)
        raster_layer.setRenderer(renderer)
        raster_layer.triggerRepaint()
    except Exception as err:
        print(f"Avertissement rendu ombrage : {err}", file=sys.stderr)


def apply_style_to_assemblage(vector_layer):
    """Applique une symbologie sobre et les etiquettes au 1:100 000."""
    if not HAS_QGIS or not vector_layer or not vector_layer.isValid():
        return
    try:
        symbol = QgsFillSymbol.createSimple({
            "color": "0,0,0,0",
            "outline_color": "#1565c0",
            "outline_width": "0.4",
            "outline_style": "solid",
        })
        vector_layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        lbl_settings = QgsPalLayerSettings()
        lbl_settings.fieldName = "identifiant"
        lbl_settings.placement = QgsPalLayerSettings.AroundPoint
        lbl_settings.scaleVisibility = True
        lbl_settings.maximumScale = 100000

        text_format = QgsTextFormat()
        text_format.setSize(9)
        text_format.setColor(QColor("#0d47a1"))
        font = text_format.font()
        font.setBold(True)
        text_format.setFont(font)

        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setSize(1.0)
        buffer.setColor(QColor(255, 255, 255, 220))
        text_format.setBuffer(buffer)

        lbl_settings.setFormat(text_format)
        vector_layer.setLabeling(QgsVectorLayerSimpleLabeling(lbl_settings))
        vector_layer.setLabelsEnabled(True)
        vector_layer.triggerRepaint()
    except Exception as err:
        print(f"Avertissement symbologie vecteur : {err}", file=sys.stderr)


class LidarDownloaderTask(QgsTask):
    """Tache d'arriere-plan QGIS pour le telechargement non-bloquant."""

    def __init__(
        self,
        tiles_list,
        output_dir,
        compress=True,
        load_in_canvas=True,
        threads=3,
        create_vrt=True,
        vrt_filename="mosaique_mnt.vrt",
        create_gpkg=True,
        gpkg_filename="tableau_assemblage_dalles.gpkg",
    ):
        super().__init__(
            f"Telechargement LiDAR HD ({len(tiles_list)} dalles)",
            QgsTask.CanCancel,
        )
        self.bridge = TaskBridge()
        self.tiles_list = tiles_list
        self.output_dir = output_dir
        self.compress = compress
        self.load_in_canvas = load_in_canvas
        self.threads = threads
        self.create_vrt = create_vrt
        self.vrt_filename = vrt_filename
        self.create_gpkg = create_gpkg
        self.gpkg_filename = gpkg_filename

        self.success_count = 0
        self.skipped_count = 0
        self.empty_count = 0
        self.error_count = 0
        self.downloaded_files = []

    def run(self):
        """Execute le telechargement dans le thread secondaire."""
        total = len(self.tiles_list)
        if total == 0:
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
            future_to_tile = {
                executor.submit(
                    download_single_tile,
                    x,
                    y,
                    self.output_dir,
                    self.compress,
                    True,
                    False,
                ): (x, y)
                for (x, y) in self.tiles_list
            }

            for idx, future in enumerate(concurrent.futures.as_completed(future_to_tile), 1):
                if self.isCanceled():
                    return False

                coords = future_to_tile[future]
                try:
                    fname, fpath, status, fsize = future.result()
                    if status == "telecharge":
                        self.success_count += 1
                        if fpath:
                            self.downloaded_files.append(fpath)
                    elif status == "deja present":
                        self.skipped_count += 1
                        if fpath:
                            self.downloaded_files.append(fpath)
                    elif "hors couverture" in status:
                        self.empty_count += 1
                    else:
                        self.error_count += 1

                    self.bridge.tile_progress.emit(idx, total, fname, status, fsize)

                except Exception as exc:
                    self.error_count += 1
                    self.bridge.tile_progress.emit(idx, total, f"{coords}", f"erreur ({exc})", 0)

                self.setProgress((idx / total) * 100.0)

        return True

    def finished(self, result):
        """Appele dans le thread principal lors de l'achevement."""
        # 1. Recuperation de toutes les dalles GeoTIFF valides dans le dossier de destination
        all_tifs = sorted(glob.glob(os.path.join(self.output_dir, "LHD_FXX_*.tif")))

        vrt_path = None
        if self.create_vrt and all_tifs:
            vrt_dest = os.path.join(self.output_dir, self.vrt_filename)
            vrt_path = build_vrt(all_tifs, vrt_dest)

        gpkg_path = None
        if self.create_gpkg and all_tifs:
            gpkg_dest = os.path.join(self.output_dir, self.gpkg_filename)
            gpkg_path = create_assemblage_gpkg(all_tifs, gpkg_dest)

        # 2. Integration dans l'arbre des couches de QGIS
        if result and self.load_in_canvas and HAS_QGIS and all_tifs:
            try:
                project = QgsProject.instance()
                root = project.layerTreeRoot()
                group = root.findGroup("MNT LiDAR HD")
                if group is None:
                    group = root.addGroup("MNT LiDAR HD")

                # A. Tableau d'assemblage (en haut du groupe, visible et etiquete)
                if gpkg_path and os.path.exists(gpkg_path):
                    gpkg_layer = QgsVectorLayer(
                        f"{gpkg_path}|layername=tableau_assemblage",
                        "Tableau d'assemblage (dallage)",
                        "ogr",
                    )
                    if gpkg_layer.isValid():
                        project.addMapLayer(gpkg_layer, False)
                        node_gpkg = group.insertLayer(0, gpkg_layer)
                        node_gpkg.setItemVisibilityChecked(True)
                        apply_style_to_assemblage(gpkg_layer)

                # B. Raster virtuel (ombrage de relief continu, visible)
                if vrt_path and os.path.exists(vrt_path):
                    vrt_layer = QgsRasterLayer(vrt_path, "MNT LiDAR HD (Ombrage)")
                    if vrt_layer.isValid():
                        project.addMapLayer(vrt_layer, False)
                        node_vrt = group.insertLayer(1 if gpkg_path else 0, vrt_layer)
                        node_vrt.setItemVisibilityChecked(True)
                        apply_hillshade_to_raster(vrt_layer)

                # C. Sous-groupe des dalles individuelles (decochees par defaut)
                sub_group = group.findGroup("Dalles individuelles")
                if sub_group is None:
                    sub_group = group.addGroup("Dalles individuelles")

                for fpath in all_tifs:
                    bname = os.path.splitext(os.path.basename(fpath))[0]
                    # Ne pas rajouter si deja present
                    existing_layer = None
                    for l in project.mapLayersByName(bname):
                        if l.source() == fpath:
                            existing_layer = l
                            break

                    if not existing_layer:
                        layer = QgsRasterLayer(fpath, bname)
                        if layer.isValid():
                            project.addMapLayer(layer, False)
                            node_tile = sub_group.addLayer(layer)
                            node_tile.setItemVisibilityChecked(False)
            except Exception as err:
                print(f"Erreur chargement couches : {err}", file=sys.stderr)

        self.bridge.task_finished.emit(
            result,
            self.success_count,
            self.skipped_count,
            self.empty_count,
            self.error_count,
        )
