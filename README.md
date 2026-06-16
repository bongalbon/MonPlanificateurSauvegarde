# Système de Planification de Sauvegarde et Restauration

Ce projet est une application de planification et de restauration de sauvegardes. Il se compose de deux parties principales :
1. **Backend** : Une API Django qui gère la planification des sauvegardes, l'exécution des tâches et la persistance des données.
2. **Frontend** : Une application de bureau Electron qui offre une interface utilisateur moderne et intuitive.

---

## Prérequis

Avant de commencer, assurez-vous d'avoir installé sur votre machine :
- [Python 3.10+](https://www.python.org/downloads/)
- [Node.js v18+](https://nodejs.org/)
- [Git](https://git-scm.com/)

---

## 🛠️ Configuration et Lancement du Backend (Django)

Le backend utilise un environnement virtuel Python pour isoler ses dépendances.

### 1. Activer l'environnement virtuel

Un dossier `venv` est déjà présent à la racine du projet. Activez-le en fonction de votre terminal :

- **PowerShell (Windows) :**
  ```powershell
  .\venv\Scripts\Activate.ps1
  ```
- **Invite de commandes (cmd - Windows) :**
  ```cmd
  venv\Scripts\activate.bat
  ```
- **Git Bash / Linux / macOS :**
  ```bash
  source venv/bin/activate
  # ou sous Windows Git Bash :
  source venv/Scripts/activate
  ```

### 2. Installer les dépendances Python

Une fois l'environnement virtuel activé, installez les paquets requis :
```bash
pip install -r requirements.txt
```

### 3. Appliquer les migrations de base de données

Appliquez les migrations Django pour initialiser la base de données SQLite (`db.sqlite3`) :
```bash
python manage.py migrate
```

### 4. Démarrer le serveur de développement

Lancez le serveur Django (par défaut accessible sur `http://127.0.0.1:8000`) :
```bash
python manage.py runserver
```

---

## 💻 Configuration et Lancement du Frontend (Electron)

L'application frontend se trouve dans le sous-dossier `electron_app`.

### 1. Naviguer vers le dossier frontend

Ouvrez un nouveau terminal et positionnez-vous dans le dossier `electron_app` :
```bash
cd electron_app
```

### 2. Installer les dépendances Node.js

Installez les modules nécessaires (notamment Electron et Electron-Builder) :
```bash
npm install
```

### 3. Démarrer l'application en mode développement

Lancez l'interface d'Electron :
```bash
npm start
```
*Note : Assurez-vous que le serveur Django backend tourne en parallèle pour que l'application puisse communiquer avec les API.*

---

## 🚀 Compilation et Packaging (Production)

Pour compiler et générer l'exécutable autonome de l'application de bureau Electron :

1. Allez dans le dossier `electron_app` :
   ```bash
   cd electron_app
   ```
2. Lancez la compilation :
   ```bash
   npm run build
   ```
Les fichiers d'installation compilés (exécutables `.exe` sous Windows) seront générés dans le sous-dossier `electron_app/dist/`.

---

## 🧪 Tests

### Tests du Backend (Django)

Pour exécuter la suite de tests unitaires et d'intégration du serveur Django :
1. Activez l'environnement virtuel (si ce n'est pas déjà fait).
2. Exécutez la commande suivante à la racine du projet :
   ```bash
   python manage.py test
   ```

### Tests du Frontend (Electron)

Pour exécuter les tests du frontend (s'ils sont configurés dans `package.json`) :
1. Allez dans le dossier `electron_app` :
   ```bash
   cd electron_app
   ```
2. Lancez la commande :
   ```bash
   npm test
   ```
