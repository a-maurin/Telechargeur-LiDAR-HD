#!/usr/bin/env python3
"""
telecharger_mnt_lidar.py
Téléchargement automatisé et fiabilisé des dalles MNT LiDAR HD 0m50 de l'IGN.
Source : service WMS-Raster officiel de la Géoplateforme IGN (data.geopf.fr).
"""

import argparse
import concurrent.futures
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()

WMS_BASE_URL = "https://data.geopf.fr/wms-r"
LAYER_NAME = "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASSIN_PATH = os.path.join(SCRIPT_DIR, "../SIG/bv_armancon.gpkg")
DEFAULT_SOUS_BASSINS_PATH = os.path.join(
    SCRIPT_DIR, "../pariries_permanentes/donnees_complementaires/sous_bassins_armancon.gpkg"
)


def tile_coords_to_bbox(x_km, y_km):
    """Calcule l'emprise exacte au demi-pixel près en Lambert-93 (EPSG:2154).
    (x_km, y_km) correspond au coin supérieur gauche de la dalle (Xmin, Ymax).
    """
    xmin = x_km * 1000.0 - 0.25
    xmax = (x_km + 1) * 1000.0 - 0.25
    ymin = (y_km - 1) * 1000.0 + 0.25
    ymax = y_km * 1000.0 + 0.25
    return xmin, ymin, xmax, ymax


def build_wms_url(x_km, y_km, filename):
    """Construit la requête WMS GetMap officielle."""
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
    """Vérifie qu'un fichier est un GeoTIFF intègre de 2000x2000 pixels.
    Retourne (est_valide: bool, est_vide_nodata: bool).
    """
    ds = None
    try:
        ds = gdal.Open(filepath, gdal.GA_ReadOnly)
        if ds is None:
            return False, False
        if ds.RasterXSize != 2000 or ds.RasterYSize != 2000:
            return False, False

        band = ds.GetRasterBand(1)
        # Force la lecture physique de tous les blocs pour détecter les fichiers tronqués
        min_val, max_val = band.ComputeRasterMinMax(False)

        if reject_empty:
            nodata = band.GetNoDataValue()
            # Si tous les pixels valent NoData (ou -99999 par défaut IGN), la dalle est vide
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
    """Calcule les statistiques exactes et génère le fichier .aux.xml."""
    ds = None
    try:
        ds = gdal.Open(filepath, gdal.GA_ReadOnly)
        if ds:
            band = ds.GetRasterBand(1)
            # False = calcul exact sur tous les pixels, True = forcer le calcul
            band.GetStatistics(False, True)
    except Exception as err:
        print(f"Avertissement statistiques pour {filepath} : {err}", file=sys.stderr)
    finally:
        ds = None


def compute_md5(filepath):
    """Calcule l'empreinte MD5 d'un fichier par blocs pour économiser la RAM."""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def download_single_tile(
    x_km,
    y_km,
    output_dir,
    compress=False,
    compute_stats=True,
    keep_empty=False,
    max_retries=3,
):
    """Télécharge une dalle unique avec reprise, lecture des blocs et contrôle NoData."""
    filename = f"LHD_FXX_{x_km:04d}_{y_km:04d}_MNT_O_0M50_LAMB93_IGN69.tif"
    final_path = os.path.join(output_dir, filename)
    aux_path = final_path + ".aux.xml"

    # 1. Vérifier si la dalle existe déjà et est valide (seuil abaissé à 10 Ko pour gérer le DEFLATE)
    if os.path.exists(final_path) and os.path.getsize(final_path) > 10240:
        is_valid, _ = verify_geotiff(final_path, reject_empty=not keep_empty)
        if is_valid:
            if compute_stats and not os.path.exists(aux_path):
                generate_aux_xml(final_path)
            return filename, "deja present", os.path.getsize(final_path)

    url = build_wms_url(x_km, y_km, filename)
    tmp_path = os.path.join(output_dir, f".{filename}.{os.getpid()}.tmp")
    headers = {"User-Agent": "Mozilla/5.0 (IGN LiDAR HD Downloader)"}

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if "xml" in content_type or "html" in content_type:
                    err_msg = response.read().decode("utf-8", errors="ignore")
                    raise RuntimeError(f"Erreur WMS du serveur : {err_msg[:200]}")

                with open(tmp_path, "wb") as f_out:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        f_out.write(chunk)

            # 2. Vérification de l'intégrité des blocs et de la couverture LiDAR HD
            is_valid, is_empty = verify_geotiff(tmp_path, reject_empty=not keep_empty)
            if is_empty:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                return filename, "hors couverture (NoData)", 0

            if not is_valid:
                raise RuntimeError("Fichier GeoTIFF tronqué, corrompu ou dimensions invalides.")

            # 3. Compression optionnelle sans perte avec tuilage interne
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
                ds_out = None  # Fermeture explicite pour vider le cache sur disque
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            else:
                os.replace(tmp_path, final_path)

            # 4. Génération des statistiques exactes (.aux.xml)
            if compute_stats:
                generate_aux_xml(final_path)

            return filename, "telecharge", os.path.getsize(final_path)

        except Exception as err:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            if attempt == max_retries:
                return filename, f"echec ({err})", 0
            time.sleep(2 * attempt)

    return filename, "echec (inconnu)", 0


def get_intersecting_tiles_from_geom(geom_2154):
    """Calcule les dalles 1km x 1km (Xmin, Ymax) intersectant une géométrie en EPSG:2154."""
    env = geom_2154.GetEnvelope()
    xmin, xmax, ymin, ymax = env[0], env[1], env[2], env[3]

    x_start = int(xmin // 1000)
    x_end = int(xmax // 1000) + 1
    y_start = int(ymin // 1000) + 1
    y_end = int(ymax // 1000) + 2

    intersecting = []
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

            if geom_2154.Intersects(tile_poly):
                intersecting.append((x, y))

    return intersecting


def get_intersecting_tiles_from_bassin(bassin_path):
    """Identifie toutes les dalles kilométriques intersectant les entités du bassin versant."""
    if not os.path.exists(bassin_path):
        raise FileNotFoundError(f"Fichier introuvable : {bassin_path}")

    ds = ogr.Open(bassin_path)
    if not ds:
        raise RuntimeError(f"Impossible d'ouvrir le fichier vecteur : {bassin_path}")

    layer = ds.GetLayer()
    srs_in = layer.GetSpatialRef()

    srs_out = osr.SpatialReference()
    srs_out.ImportFromEPSG(2154)
    srs_out.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    transform = None
    if srs_in is not None:
        srs_in = srs_in.Clone()
        srs_in.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if not srs_in.IsSame(srs_out):
            transform = osr.CoordinateTransformation(srs_in, srs_out)

    # Collecte de toutes les entités géométriques de la couche
    multi_geom = ogr.Geometry(ogr.wkbGeometryCollection)
    layer.ResetReading()
    for feature in layer:
        geom = feature.GetGeometryRef()
        if geom is not None and not geom.IsEmpty():
            g_clone = geom.Clone()
            if srs_in is None and g_clone.GetSpatialReference() is not None:
                g_srs = g_clone.GetSpatialReference().Clone()
                g_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
                if not g_srs.IsSame(srs_out):
                    g_clone.Transform(osr.CoordinateTransformation(g_srs, srs_out))
            elif transform is not None:
                g_clone.Transform(transform)
            multi_geom.AddGeometry(g_clone)

    ds = None
    if multi_geom.IsEmpty():
        raise ValueError(f"Aucune géométrie valide trouvée dans : {bassin_path}")

    return get_intersecting_tiles_from_geom(multi_geom)


def list_sous_bassins(gpkg_path=DEFAULT_SOUS_BASSINS_PATH):
    """Affiche la liste détaillée des sous-bassins disponibles."""
    if not os.path.exists(gpkg_path):
        raise FileNotFoundError(f"Fichier de sous-bassins introuvable : {gpkg_path}")

    ds = ogr.Open(gpkg_path)
    if not ds:
        raise RuntimeError(f"Impossible d'ouvrir le fichier vecteur : {gpkg_path}")

    layer = ds.GetLayer()
    print("\nSous-bassins disponibles dans le bassin de l'Armançon :")
    layer.ResetReading()
    for feat in layer:
        nom = feat.GetField("sous_bassin")
        surf = feat.GetField("surface_km2")
        geom = feat.GetGeometryRef()
        tiles = get_intersecting_tiles_from_geom(geom)
        print(f"  * {nom:50s} : {surf:6.1f} km² -> {len(tiles):4d} dalles")
    print()
    ds = None


def get_intersecting_tiles_from_sous_bassin(search_term, gpkg_path=DEFAULT_SOUS_BASSINS_PATH):
    """Filtre les dalles pour un sous-bassin spécifique."""
    if not os.path.exists(gpkg_path):
        raise FileNotFoundError(f"Fichier de sous-bassins introuvable : {gpkg_path}")

    ds = ogr.Open(gpkg_path)
    if not ds:
        raise RuntimeError(f"Impossible d'ouvrir le fichier vecteur : {gpkg_path}")

    layer = ds.GetLayer()
    srs_in = layer.GetSpatialRef()
    srs_out = osr.SpatialReference()
    srs_out.ImportFromEPSG(2154)
    srs_out.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    transform = None
    if srs_in is not None:
        srs_in = srs_in.Clone()
        srs_in.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if not srs_in.IsSame(srs_out):
            transform = osr.CoordinateTransformation(srs_in, srs_out)

    multi_geom = ogr.Geometry(ogr.wkbGeometryCollection)
    matched_names = []
    total_surface = 0.0

    layer.ResetReading()
    for feature in layer:
        nom = feature.GetField("sous_bassin")
        if nom and search_term.lower() in nom.lower():
            matched_names.append(nom)
            surf = feature.GetField("surface_km2")
            if surf is not None:
                total_surface += float(surf)
            geom = feature.GetGeometryRef()
            if geom is not None and not geom.IsEmpty():
                g_clone = geom.Clone()
                if transform is not None:
                    g_clone.Transform(transform)
                multi_geom.AddGeometry(g_clone)

    ds = None
    if multi_geom.IsEmpty():
        raise ValueError(
            f"Aucun sous-bassin correspondant à '{search_term}'. "
            f"Utilisez --liste-sous-bassins pour afficher les noms exacts."
        )

    print(f"Sous-bassin ciblé : {', '.join(matched_names)} ({total_surface:.1f} km²)")
    return get_intersecting_tiles_from_geom(multi_geom)


def parse_tile_string(tile_str):
    """Convertit une chaîne '0781_6748' ou un nom de fichier complet en (X, Y)."""
    base = os.path.basename(tile_str)
    parts = base.replace(".tif", "").split("_")
    for i in range(len(parts) - 1):
        if parts[i].isdigit() and parts[i + 1].isdigit():
            if len(parts[i]) == 4 and len(parts[i + 1]) == 4:
                return int(parts[i]), int(parts[i + 1])
    raise ValueError(f"Format de dalle non reconnu : {tile_str}")


def main():
    parser = argparse.ArgumentParser(
        description="Téléchargement des dalles MNT LiDAR HD 0m50 (IGN)."
    )
    parser.add_argument(
        "--tiles",
        nargs="+",
        help="Liste de dalles à télécharger (ex: 0781_6748 0782_6748)",
    )
    parser.add_argument(
        "--bassin",
        default=DEFAULT_BASSIN_PATH,
        help="Chemin vers le fichier vecteur du bassin versant",
    )
    parser.add_argument(
        "--sous-bassin",
        help="Nom (ou partie du nom) du sous-bassin à cibler (ex: 'Aisy', 'Armançon amont')",
    )
    parser.add_argument(
        "--liste-sous-bassins",
        action="store_true",
        help="Lister tous les sous-bassins disponibles avec leurs surfaces et quitter",
    )
    parser.add_argument(
        "--fichier-sous-bassins",
        default=DEFAULT_SOUS_BASSINS_PATH,
        help="Chemin vers le fichier vecteur des sous-bassins",
    )
    parser.add_argument(
        "--output-dir",
        default="./dalles_mnt",
        help="Répertoire de destination des dalles",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=3,
        help="Nombre de téléchargements simultanés (3 recommandé)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Nombre maximal de dalles à télécharger",
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        help="Activer la compression GeoTIFF DEFLATE sans perte (gain 50-70%%)",
    )
    parser.add_argument(
        "--stats",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Générer les fichiers statistiques .aux.xml (activé par défaut, --no-stats pour désactiver)",
    )
    parser.add_argument(
        "--keep-empty",
        action="store_true",
        help="Conserver les dalles composées à 100%% de NoData (hors couverture LiDAR HD)",
    )
    parser.add_argument(
        "--verify",
        help="Chemin d'un fichier de référence pour vérification par empreinte MD5",
    )

    args = parser.parse_args()
    if args.liste_sous_bassins:
        list_sous_bassins(args.fichier_sous_bassins)
        return

    os.makedirs(args.output_dir, exist_ok=True)

    # Déterminer la liste des dalles
    if args.tiles:
        tiles_list = [parse_tile_string(t) for t in args.tiles]
    elif args.sous_bassin:
        print(f"Extraction des dalles pour le sous-bassin '{args.sous_bassin}'...")
        tiles_list = get_intersecting_tiles_from_sous_bassin(
            args.sous_bassin, args.fichier_sous_bassins
        )
        print(f"Total dalles identifiées : {len(tiles_list)}")
    else:
        print(f"Calcul des dalles à partir du bassin : {args.bassin}")
        tiles_list = get_intersecting_tiles_from_bassin(args.bassin)
        print(f"Total dalles identifiées : {len(tiles_list)}")

    if args.limit:
        tiles_list = tiles_list[: args.limit]
        print(f"Limite appliquée : {len(tiles_list)} dalles")

    total = len(tiles_list)
    if total == 0:
        print("Aucune dalle à télécharger.")
        return

    print(f"Démarrage du téléchargement vers : {args.output_dir}")
    print(
        f"Paramètres : threads={args.threads}, compression={args.compress}, "
        f"stats={args.stats}, keep_empty={args.keep_empty}"
    )

    success_count = 0
    skipped_count = 0
    empty_count = 0
    error_count = 0
    total_bytes = 0

    start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as executor:
        future_to_tile = {
            executor.submit(
                download_single_tile,
                x,
                y,
                args.output_dir,
                args.compress,
                args.stats,
                args.keep_empty,
            ): (x, y)
            for (x, y) in tiles_list
        }

        for idx, future in enumerate(concurrent.futures.as_completed(future_to_tile), 1):
            tile_coords = future_to_tile[future]
            try:
                fname, status, fsize = future.result()
                if status == "telecharge":
                    success_count += 1
                    total_bytes += fsize
                elif status == "deja present":
                    skipped_count += 1
                    total_bytes += fsize
                elif "hors couverture" in status:
                    empty_count += 1
                else:
                    error_count += 1

                print(
                    f"[{idx}/{total}] {fname} -> {status} "
                    f"({fsize / (1024 * 1024):.1f} Mo)"
                )
            except Exception as exc:
                error_count += 1
                print(f"[{idx}/{total}] Erreur dalle {tile_coords} : {exc}")

    elapsed = time.time() - start_time
    print("\n--- Bilan du téléchargement ---")
    print(f"Dalles téléchargées avec succès : {success_count}")
    print(f"Dalles déjà présentes           : {skipped_count}")
    print(f"Dalles vides (hors couverture)  : {empty_count}")
    print(f"Échecs                          : {error_count}")
    print(f"Volume total sur disque         : {total_bytes / (1024 * 1024):.1f} Mo")
    print(f"Durée d'exécution               : {elapsed:.1f} secondes")

    # Vérification MD5 optionnelle
    if args.verify:
        if os.path.exists(args.verify):
            ref_md5 = compute_md5(args.verify)
            target_file = os.path.join(args.output_dir, os.path.basename(args.verify))
            if os.path.exists(target_file):
                dl_md5 = compute_md5(target_file)
                print("\n--- Contrôle d'intégrité MD5 ---")
                print(f"Fichier source     : {ref_md5}")
                print(f"Fichier téléchargé : {dl_md5}")
                if ref_md5 == dl_md5:
                    print("Conformité validée : les deux fichiers sont rigoureusement identiques.")
                else:
                    print("Avertissement : empreintes différentes (compression ou version différente).")
            else:
                print(f"Fichier {target_file} introuvable pour vérification.")
        else:
            print(f"Fichier de référence {args.verify} introuvable.")


if __name__ == "__main__":
    main()