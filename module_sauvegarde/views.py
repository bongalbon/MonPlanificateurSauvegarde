import threading
from rest_framework import viewsets, status, serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from module_sauvegarde.models import ProjetSauvegarde, DestinationSauvegarde, HistoriqueSauvegarde
from module_sauvegarde.services import executer_sauvegarde_projet, executer_restoration_projet


# --- SERIALIZERS ---

class DestinationSauvegardeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DestinationSauvegarde
        fields = '__all__'


class ProjetSauvegardeSerializer(serializers.ModelSerializer):
    destinations = DestinationSauvegardeSerializer(many=True, read_only=True)

    class Meta:
        model = ProjetSauvegarde
        fields = '__all__'


class HistoriqueSauvegardeSerializer(serializers.ModelSerializer):
    class Meta:
        model = HistoriqueSauvegarde
        fields = '__all__'


# --- VIEWSETS ---

class ProjetSauvegardeViewSet(viewsets.ModelViewSet):
    """ViewSet pour gérer les projets de sauvegarde."""
    queryset = ProjetSauvegarde.objects.all()
    serializer_class = ProjetSauvegardeSerializer

    @action(detail=True, methods=['post'])
    def sauvegarder(self, request, pk=None):
        """Déclenche instantanément et de manière synchrone une sauvegarde pour ce projet."""
        project = self.get_object()
        try:
            historique = executer_sauvegarde_projet(project.id)
            if historique and historique.statut == 'succes':
                return Response({
                    "status": "succes",
                    "message": "Sauvegarde effectuée avec succès.",
                    "historique_id": historique.id,
                    "chemin_archive": historique.chemin_archive_generee
                }, status=status.HTTP_200_OK)
            else:
                log_msg = historique.message_log if historique else "Aucun log disponible."
                return Response({
                    "status": "echec",
                    "message": "Échec de la sauvegarde.",
                    "log": log_msg
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            return Response({
                "status": "echec",
                "message": f"Une exception s'est produite lors du déclenchement de la sauvegarde : {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def restaurer_fichier(self, request, pk=None):
        """Déclenche la procédure de restauration depuis un fichier ZIP direct en arrière-plan."""
        project = self.get_object()
        zip_file_path = request.data.get('chemin_archive')
        
        if not zip_file_path:
            return Response({
                "status": "echec",
                "message": "Le chemin de l'archive ZIP à restaurer doit être fourni."
            }, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            import threading
            from module_sauvegarde.services import executer_restoration_projet_depuis_fichier
            
            thread = threading.Thread(
                target=executer_restoration_projet_depuis_fichier,
                args=(project.id, zip_file_path),
                daemon=True
            )
            thread.start()
            
            return Response({
                "status": "succes",
                "message": "La procédure de restauration depuis le fichier a été lancée en arrière-plan."
            }, status=status.HTTP_202_ACCEPTED)
        except Exception as e:
            return Response({
                "status": "echec",
                "message": f"Erreur lors du lancement de la restauration : {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DestinationSauvegardeViewSet(viewsets.ModelViewSet):
    """ViewSet pour gérer les destinations associées aux projets."""
    queryset = DestinationSauvegarde.objects.all()
    serializer_class = DestinationSauvegardeSerializer


class HistoriqueSauvegardeViewSet(viewsets.ModelViewSet):
    """ViewSet pour consulter l'historique et lancer les restaurations."""
    queryset = HistoriqueSauvegarde.objects.all()
    serializer_class = HistoriqueSauvegardeSerializer

    @action(detail=True, methods=['post'])
    def restaurer(self, request, pk=None):
        """Déclenche la procédure de restauration en arrière-plan à partir de cette sauvegarde."""
        historique = self.get_object()
        
        # S'assurer qu'on restaure une archive de sauvegarde valide
        if historique.type_action != 'sauvegarde':
            return Response({
                "status": "echec",
                "message": "La restauration doit être basée sur une action de type 'sauvegarde'."
            }, status=status.HTTP_400_BAD_REQUEST)
            
        if historique.statut != 'succes':
            return Response({
                "status": "echec",
                "message": "Impossible de restaurer à partir d'une sauvegarde en échec."
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Lancement asynchrone dans un thread de fond
            thread = threading.Thread(
                target=executer_restoration_projet,
                args=(historique.id,),
                daemon=True
            )
            thread.start()
            
            return Response({
                "status": "succes",
                "message": "La procédure de restauration complète de la base et des médias a été lancée en arrière-plan."
            }, status=status.HTTP_202_ACCEPTED)
        except Exception as e:
            return Response({
                "status": "echec",
                "message": f"Erreur lors du lancement du processus de restauration : {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
