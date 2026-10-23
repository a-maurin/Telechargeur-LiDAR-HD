"""
Point d'entree du plugin QGIS Telechargeur LiDAR HD.
"""


def classFactory(iface):
    """Charge la classe principale de l'extension."""
    from .telechargeur_plugin import TelechargeurLidarPlugin

    return TelechargeurLidarPlugin(iface)
