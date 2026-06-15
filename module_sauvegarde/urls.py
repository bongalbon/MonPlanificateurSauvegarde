from django.urls import path, include
from rest_framework.routers import DefaultRouter
from module_sauvegarde.views import ProjetSauvegardeViewSet, DestinationSauvegardeViewSet, HistoriqueSauvegardeViewSet

router = DefaultRouter()
router.register(r'projet', ProjetSauvegardeViewSet, basename='projet')
router.register(r'destination', DestinationSauvegardeViewSet, basename='destination')
router.register(r'historique', HistoriqueSauvegardeViewSet, basename='historique')

urlpatterns = [
    path('', include(router.urls)),
]
