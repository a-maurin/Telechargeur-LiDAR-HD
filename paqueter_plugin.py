#!/usr/bin/env python3
"""
paqueter_plugin.py
Script de generation du fichier ZIP installable pour QGIS (Windows et Linux).
"""

import os
import zipfile


def paqueter():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    plugin_src_dir = os.path.join(base_dir, "qgis_plugin", "telechargeur_lidar_hd")
    zip_output_path = os.path.join(base_dir, "telechargeur_lidar_hd.zip")

    if not os.path.isdir(plugin_src_dir):
        raise FileNotFoundError(f"Dossier source introuvable : {plugin_src_dir}")

    print(f"Emballage de l'extension depuis : {plugin_src_dir}")
    print(f"Destination de l'archive : {zip_output_path}")

    # Fichiers a exclure
    excludes = {".pyc", ".tmp", ".git", ".DS_Store"}

    with zipfile.ZipFile(zip_output_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(plugin_src_dir):
            # Ne pas parcourir les dossiers exclus
            dirs[:] = [d for d in dirs if not any(ex in d for ex in excludes) and d != "__pycache__"]

            for file in files:
                if any(file.endswith(ex) for ex in excludes):
                    continue

                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, os.path.dirname(plugin_src_dir))
                zipf.write(full_path, rel_path)
                print(f"  + {rel_path}")

    print(f"\nArchive creee avec succes : {zip_output_path}")
    print(f"Poids de l'archive : {os.path.getsize(zip_output_path) / 1024:.1f} Ko")

    # Verification de la structure interne
    with zipfile.ZipFile(zip_output_path, "r") as zipf:
        namelist = zipf.namelist()
        required = [
            "telechargeur_lidar_hd/metadata.txt",
            "telechargeur_lidar_hd/__init__.py",
            "telechargeur_lidar_hd/telechargeur_plugin.py",
            "telechargeur_lidar_hd/telechargeur_dialog.py",
            "telechargeur_lidar_hd/telechargeur_task.py",
            "telechargeur_lidar_hd/icon.png",
            "telechargeur_lidar_hd/LICENSE",
        ]
        for req in required:
            if req not in namelist:
                raise RuntimeError(f"Fichier requis manquant dans le ZIP : {req}")

    print("Structure interne conforme aux exigences officielles de QGIS.")


if __name__ == "__main__":
    paqueter()
