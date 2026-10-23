"""
telechargeur_plugin.py
Classe principale de l'extension QGIS Telechargeur LiDAR HD.
"""

import os
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .telechargeur_dialog import TelechargeurDialog

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


class TelechargeurLidarPlugin:
    """Extension QGIS principale integrant l'action dans l'interface."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.toolbar = None

    def initGui(self):
        """Initialise l'action graphique dans la barre d'outils et les menus."""
        icon_path = os.path.join(PLUGIN_DIR, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self.action = QAction(icon, "Téléchargeur LiDAR HD (MNT 0m50)", self.iface.mainWindow())
        self.action.setStatusTip("Téléchargement automatisé des dalles MNT LiDAR HD")
        self.action.triggered.connect(self.run)

        # 1. Cree sa propre barre d'outils dediee pour etre immanquable
        self.toolbar = self.iface.addToolBar("Téléchargeur LiDAR HD")
        self.toolbar.setObjectName("TelechargeurLidarHDToolBar")
        self.toolbar.addAction(self.action)

        # 2. Ajout aux barres d'outils standard
        self.iface.addToolBarIcon(self.action)
        self.iface.addRasterToolBarIcon(self.action)

        # 3. Ajout aux menus
        self.iface.addPluginToMenu("Téléchargeur LiDAR HD", self.action)
        self.iface.addPluginToRasterMenu("Téléchargeur LiDAR HD", self.action)

    def unload(self):
        """Nettoie les actions lors de la desactivation du plugin."""
        if self.toolbar:
            del self.toolbar
            self.toolbar = None
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removeRasterToolBarIcon(self.action)
            self.iface.removePluginMenu("Téléchargeur LiDAR HD", self.action)
            self.iface.removePluginRasterMenu("Téléchargeur LiDAR HD", self.action)
            self.action = None

        if self.dialog:
            self.dialog.close()
            self.dialog = None

    def run(self):
        """Ouvre la fenetre de dialogue du plugin."""
        if not self.dialog:
            self.dialog = TelechargeurDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
