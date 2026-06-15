import logging
from django.utils import timezone
# pyrefly: ignore [missing-import]
from apscheduler.schedulers.background import BackgroundScheduler
# pyrefly: ignore [missing-import]
from django_apscheduler.jobstores import DjangoJobStore, register_events

logger = logging.getLogger(__name__)

# Instance globale unique du planificateur
scheduler = None


def demarrer_planificateur():
    """Initialise et démarre le planificateur de tâches de fond."""
    global scheduler
    if scheduler is not None and scheduler.running:
        logger.info("Le planificateur de sauvegardes est déjà actif.")
        return scheduler

    scheduler = BackgroundScheduler()
    scheduler.add_jobstore(DjangoJobStore(), "default")
    register_events(scheduler)
    scheduler.start()
    logger.info("Le planificateur de sauvegardes a démarré avec succès.")

    # Chargement de tous les projets de sauvegarde actifs (en arrière-plan pour éviter RuntimeWarning au démarrage)
    import threading
    import time
    
    def charger_jobs_au_demarrage():
        time.sleep(0.1)  # Laisser Django terminer complètement son initialisation
        import django.db.utils
        try:
            from module_sauvegarde.models import ProjetSauvegarde
            for project in ProjetSauvegarde.objects.filter(est_actif=True):
                recharger_configuration_job(project.id)
        except (django.db.utils.OperationalError, django.db.utils.ProgrammingError) as e:
            logger.warning(f"La base de données n'est pas encore prête. Chargement des tâches ignoré: {str(e)}")

    threading.Thread(target=charger_jobs_au_demarrage, daemon=True).start()
        
    return scheduler


def recharger_configuration_job(projet_id):
    """Met à jour, ajoute ou retire de manière dynamique le planning d'un projet de sauvegarde."""
    global scheduler
    if scheduler is None or not scheduler.running:
        return

    from module_sauvegarde.models import ProjetSauvegarde
    from module_sauvegarde.services import executer_sauvegarde_projet

    job_id = f"projet_{projet_id}"

    try:
        project = ProjetSauvegarde.objects.get(id=projet_id)
    except ProjetSauvegarde.DoesNotExist:
        # Le projet a été supprimé : retirer son planning s'il existe
        try:
            scheduler.remove_job(job_id)
            logger.info(f"Planification supprimée pour le projet ID {projet_id}")
        except Exception:
            pass
        return

    if not project.est_actif:
        # Le projet est désactivé : retirer son planning s'il existe
        try:
            scheduler.remove_job(job_id)
            logger.info(f"Planification désactivée pour le projet '{project.nom_projet}'")
        except Exception:
            pass
        return

    # Définition du moment du premier run (immédiat si lancer_au_demarrage=True)
    next_run = timezone.now() if project.lancer_au_demarrage else None

    # Ajout ou mise à jour du job dans APScheduler
    scheduler.add_job(
        executer_sauvegarde_projet,
        trigger="interval",
        minutes=project.frequence_minutes,
        id=job_id,
        args=[project.id],
        replace_existing=True,
        next_run_time=next_run
    )
    logger.info(f"Planification configurée pour '{project.nom_projet}' (Fréquence: {project.frequence_minutes} minutes)")
