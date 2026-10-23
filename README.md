# Téléchargeur LiDAR HD

Le Téléchargeur LiDAR HD est une solution complète permettant d'automatiser la recherche, le téléchargement et l'assemblage des dalles altimétriques de modèles numériques de terrain (MNT) à haute résolution (0,50 mètre) produites par l'IGN et diffusées sur la Géoplateforme.

Le projet propose à la fois une extension graphique pour le logiciel SIG QGIS et un script exécutable en ligne de commande pour les traitements automatisés.

## Fonctionnalités principales

L'outil simplifie l'exploitation des données altimétriques LiDAR HD en évitant les démarches manuelles d'interrogation et de téléchargement dalle par dalle :

* **Découpage spatial** : sélection automatique des dalles d'un kilomètre de côté intersectant une couche vectorielle active ou une emprise géographique définie.
* **Téléchargement** : interrogation des services de l'IGN Géoplateforme avec gestion de la reprise sur erreur, contrôle de validité des dalles reçues et compression sans perte (GeoTIFF DEFLATE).
* **Intégration dans QGIS** : exécution en tâche de fond asynchrone évitant tout gel de l'interface, assortie d'une barre de progression détaillée et d'un bouton d'annulation immédiate.
* **Assemblage cartographique** : génération automatique d'un raster virtuel (fichier VRT) fusionnant l'ensemble des dalles du secteur, avec création optionnelle d'un ombrage du relief pour une lecture morphologique instantanée.
* **Suivi et contrôle vectoriel** : export d'une couche vectorielle au format GeoPackage matérialisant le carroyage officiel des dalles et leur état de complétude.
* **Mode console autonome** : script Python indépendant permettant d'intégrer la chaîne de traitement dans des scripts batch ou des serveurs sans interface graphique.

## Architecture du dépôt

Le dépôt est structuré comme suit :

* `qgis_plugin/telechargeur_lidar_hd/` : code source complet de l'extension QGIS (interface graphique, moteur asynchrone, métadonnées et ressources).
* `telecharger_mnt_lidar.py` : script autonome en ligne de commande pour exécution directe sous Python 3.
* `paqueter_plugin.py` : script utilitaire générant l'archive ZIP installable prête pour QGIS.
* `LICENSE` : texte de la licence GNU GPL v3 et clause d'attribution.

## Prérequis

Pour utiliser l'extension QGIS :
* QGIS 3.10 ou version ultérieure (testé avec succès sur les versions 3.28 LTR et 3.34 LTR).
* Une connexion Internet active pour interroger l'API de l'IGN Géoplateforme.

Pour le script autonome en ligne de commande :
* Python 3.8 ou supérieur.
* Bibliothèques Python requises : `requests` et `gdal` (ou environnement OSGeo4W sous Windows).

## Installation dans QGIS

1. Téléchargez la dernière version de l'archive `telechargeur_lidar_hd.zip` disponible sur la page des versions du dépôt.
2. Ouvrez QGIS.
3. Dans la barre de menus, rendez-vous dans `Extensions` puis sélectionnez `Installer/Gérer les extensions`.
4. Choisissez l'onglet `Installer depuis un fichier ZIP`.
5. Parcourez votre système de fichiers pour désigner l'archive `telechargeur_lidar_hd.zip` et cliquez sur `Installer l'extension`.
6. L'icône de l'outil apparaît alors dans la barre d'outils dédiée ainsi que dans le menu `Raster`.

Si vous souhaitez modifier le code source, vous pouvez également cloner le dépôt et copier le sous-dossier `qgis_plugin/telechargeur_lidar_hd` directement dans le dossier des extensions actives de votre profil QGIS (`~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` sous Linux ou `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` sous Windows).

## Guide d'utilisation

### Utilisation via l'interface QGIS

1. Chargez votre couche vectorielle de travail (périmètre d'étude, bassin versant, tracé de cours d'eau ou limites administratives) dans QGIS.
2. Cliquez sur l'icône du Téléchargeur LiDAR HD.
3. Définissez le mode de découpage :
   * Soit par intersection avec la couche vectorielle sélectionnée.
   * Soit par emprise personnalisée.
4. Spécifiez le dossier de destination où seront stockées les dalles GeoTIFF.
5. Cochez les options de post-traitement souhaitées :
   * Création du fichier VRT d'assemblage.
   * Calcul du modèle d'ombrage du relief.
   * Génération de la couche de carroyage GeoPackage.
6. Cliquez sur `Lancer le téléchargement`. Le traitement se déroule en arrière-plan sans bloquer vos manipulations dans le logiciel.

### Utilisation en ligne de commande

Le script `telecharger_mnt_lidar.py` permet d'exécuter l'interrogation et le rapatriement directement depuis un terminal :

```bash
python3 telecharger_mnt_lidar.py --emprise minx miny maxx maxy --sortie ./dossier_mnt
```

Des options complémentaires permettent d'indiquer un fichier vectoriel en entrée, de filtrer le niveau de compression et de demander l'assemblage VRT.

## Licence

Ce programme est un logiciel libre distribué sous licence **GNU General Public License v3 (GPL-3.0)**.

Conformément à la section 7.b de cette licence, toute redistribution, réutilisation, adaptation ou incorporation partielle ou totale de ce travail impose l'obligation légale de citer explicitement la paternité de l'auteur original :

* **Auteur original** : Aguirre Maurin
* **Courriel de contact** : aguirre.maurin@murena.io
* **Dépôt officiel** : [https://github.com/a-maurin/Telechargeur-LiDAR-HD](https://github.com/a-maurin/Telechargeur-LiDAR-HD)

Cette mention doit figurer lisiblement dans la documentation, les notices techniques, les crédits des applications dérivées ainsi que dans les éventuelles publications valorisant ces travaux.
