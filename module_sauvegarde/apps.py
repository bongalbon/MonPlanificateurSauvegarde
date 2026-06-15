import os
import sys
from django.apps import AppConfig

class ModuleSauvegardeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'module_sauvegarde'

    def ready(self):
        # Éviter de lancer le planificateur deux fois lors du rechargement automatique de runserver
        is_manage_py = any('manage.py' in arg for arg in sys.argv)
        is_runserver = 'runserver' in sys.argv
        
        if is_manage_py and is_runserver:
            if os.environ.get('RUN_MAIN') == 'true':
                from module_sauvegarde.jobs import demarrer_planificateur
                demarrer_planificateur()
        else:
            from module_sauvegarde.jobs import demarrer_planificateur
            demarrer_planificateur()
