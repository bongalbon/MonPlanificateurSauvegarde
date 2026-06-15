from django.db import models

class ProjetSauvegarde(models.Model):
    nom_projet = models.CharField(max_length=255, verbose_name="Nom du projet")
    db_name = models.CharField(max_length=255, verbose_name="Nom de la base de données")
    db_user = models.CharField(max_length=255, verbose_name="Utilisateur DB")
    db_password = models.CharField(max_length=255, verbose_name="Mot de passe DB")
    db_host = models.CharField(max_length=255, default='localhost', verbose_name="Hôte DB")
    db_port = models.CharField(max_length=10, default='5432', verbose_name="Port DB")
    chemin_media_local = models.CharField(max_length=500, verbose_name="Chemin local des médias")
    frequence_minutes = models.IntegerField(default=60, verbose_name="Fréquence (minutes)")
    est_actif = models.BooleanField(default=True, verbose_name="Est actif")
    lancer_au_demarrage = models.BooleanField(default=False, verbose_name="Lancer au démarrage")

    def __str__(self):
        return self.nom_projet

    class Meta:
        verbose_name = "Projet de sauvegarde"
        verbose_name_plural = "Projets de sauvegarde"


class DestinationSauvegarde(models.Model):
    DESTINATION_CHOICES = [
        ('local', 'Local'),
        ('gdrive', 'Google Drive'),
        ('onedrive', 'OneDrive'),
    ]

    projet = models.ForeignKey(
        ProjetSauvegarde, 
        on_delete=models.CASCADE, 
        related_name='destinations', 
        verbose_name="Projet"
    )
    type_destination = models.CharField(
        max_length=50, 
        choices=DESTINATION_CHOICES, 
        verbose_name="Type de destination"
    )
    chemin_local = models.CharField(
        max_length=500, 
        null=True, 
        blank=True, 
        verbose_name="Chemin local"
    )
    token_auth_cloud = models.TextField(
        null=True, 
        blank=True, 
        verbose_name="Token Auth Cloud"
    )
    dossier_cible_cloud = models.CharField(
        max_length=255, 
        null=True, 
        blank=True, 
        verbose_name="Dossier cible Cloud (ID/Chemin)"
    )

    def __str__(self):
        return f"{self.projet.nom_projet} -> {self.type_destination}"

    class Meta:
        verbose_name = "Destination de sauvegarde"
        verbose_name_plural = "Destinations de sauvegarde"


class HistoriqueSauvegarde(models.Model):
    ACTION_CHOICES = [
        ('sauvegarde', 'Sauvegarde'),
        ('restauration', 'Restauration'),
    ]
    
    STATUT_CHOICES = [
        ('succes', 'Succès'),
        ('echec', 'Échec'),
    ]

    projet = models.ForeignKey(
        ProjetSauvegarde, 
        on_delete=models.CASCADE, 
        related_name='historiques', 
        verbose_name="Projet"
    )
    date_execution = models.DateTimeField(
        auto_now_add=True, 
        verbose_name="Date d'exécution"
    )
    type_action = models.CharField(
        max_length=50, 
        choices=ACTION_CHOICES, 
        verbose_name="Type d'action"
    )
    statut = models.CharField(
        max_length=50, 
        choices=STATUT_CHOICES, 
        verbose_name="Statut"
    )
    message_log = models.TextField(
        blank=True, 
        verbose_name="Message de log"
    )
    chemin_archive_generee = models.CharField(
        max_length=500, 
        blank=True, 
        verbose_name="Chemin de l'archive générée"
    )

    def __str__(self):
        return f"{self.projet.nom_projet} - {self.type_action} ({self.date_execution.strftime('%Y-%m-%d %H:%M')})"

    class Meta:
        verbose_name = "Historique de sauvegarde"
        verbose_name_plural = "Historiques de sauvegarde"
        ordering = ['-date_execution']


from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

@receiver(post_save, sender=ProjetSauvegarde)
def projet_sauvegarde_post_save(sender, instance, **kwargs):
    from module_sauvegarde.jobs import recharger_configuration_job
    try:
        recharger_configuration_job(instance.id)
    except Exception:
        pass

@receiver(post_delete, sender=ProjetSauvegarde)
def projet_sauvegarde_post_delete(sender, instance, **kwargs):
    from module_sauvegarde.jobs import recharger_configuration_job
    try:
        recharger_configuration_job(instance.id)
    except Exception:
        pass

