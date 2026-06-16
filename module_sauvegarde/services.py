import os
import sys
import json
import zipfile
import shutil
import tempfile
import subprocess
import requests
from datetime import datetime
from django.utils import timezone
from django.conf import settings
from module_sauvegarde.models import ProjetSauvegarde, DestinationSauvegarde, HistoriqueSauvegarde

class LogAccumulator:
    """Accumulateur de logs pour l'enregistrement en base de données."""
    def __init__(self):
        self.logs = []
    
    def log(self, message):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = f"[{timestamp}] {message}"
        self.logs.append(msg)
        print(msg)
        
    def get_content(self):
        return "\n".join(self.logs)


def find_pg_binary(binary_name):
    """Recherche les binaires de PostgreSQL dans le PATH ou dans les répertoires standards sous Windows."""
    # 1. Essai avec shutil.which (si dans le PATH)
    path_bin = shutil.which(binary_name)
    if path_bin:
        return path_bin
    
    # 2. Recherche dans les dossiers par défaut de Windows
    possible_dirs = [
        r"C:\Program Files\PostgreSQL",
        r"C:\Program Files (x86)\PostgreSQL",
    ]
    for pdir in possible_dirs:
        if os.path.exists(pdir):
            try:
                for version in sorted(os.listdir(pdir), reverse=True):
                    bin_dir = os.path.join(pdir, version, 'bin')
                    candidate = os.path.join(bin_dir, binary_name + ".exe")
                    if os.path.exists(candidate):
                        return candidate
            except Exception:
                pass
                
    # Retour par défaut
    return binary_name


# --- GOOGLE DRIVE HELPERS ---

def get_gdrive_service(token_json_str):
    """Initialise le service Google Drive v3 avec rafraîchissement automatique."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    
    token_data = json.loads(token_json_str)
    credentials = Credentials.from_authorized_user_info(token_data)
    
    if credentials.expired and credentials.refresh_token:
        from google.auth.transport.requests import Request
        credentials.refresh(Request())
        
    return build('drive', 'v3', credentials=credentials)


def upload_to_gdrive(token_json_str, file_path, folder_id=None):
    """Upload de fichier ZIP vers Google Drive avec support facultatif de dossier parent."""
    from googleapiclient.http import MediaFileUpload
    service = get_gdrive_service(token_json_str)
    
    file_metadata = {'name': os.path.basename(file_path)}
    if folder_id:
        file_metadata['parents'] = [folder_id]
        
    media = MediaFileUpload(file_path, mimetype='application/zip', resumable=True)
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return file.get('id')


def download_from_gdrive(token_json_str, file_id, dest_path):
    """Téléchargement d'un fichier depuis Google Drive."""
    from googleapiclient.http import MediaIoBaseDownload
    service = get_gdrive_service(token_json_str)
    
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()


# --- ONEDRIVE HELPERS ---

def upload_to_onedrive(token, file_path, folder_path=None):
    """Upload de fichier vers OneDrive par segments (chunked upload) pour les gros fichiers."""
    access_token = token
    if token.strip().startswith('{'):
        try:
            token_data = json.loads(token)
            access_token = token_data.get('access_token', token)
        except Exception:
            pass
            
    headers = {'Authorization': f'Bearer {access_token}'}
    filename = os.path.basename(file_path)
    
    if folder_path:
        # Standardiser le chemin cible OneDrive
        folder_path = '/' + folder_path.strip('/')
        item_path = f"{folder_path}/{filename}"
    else:
        item_path = f"/{filename}"
        
    url = f"https://graph.microsoft.com/v1.0/me/drive/root:{item_path}:/createUploadSession"
    response = requests.post(url, headers=headers, json={})
    if response.status_code not in [200, 201]:
        raise Exception(f"Impossible de créer la session d'upload OneDrive: {response.text}")
        
    upload_url = response.json()['uploadUrl']
    file_size = os.path.getsize(file_path)
    chunk_size = 3276800  # Multiples de 327 680 octets (~3.1 MB)
    
    with open(file_path, 'rb') as f:
        start = 0
        while start < file_size:
            chunk = f.read(chunk_size)
            end = start + len(chunk) - 1
            content_range = f"bytes {start}-{end}/{file_size}"
            headers_chunk = {
                'Content-Length': str(len(chunk)),
                'Content-Range': content_range
            }
            res = requests.put(upload_url, headers=headers_chunk, data=chunk)
            if end == file_size - 1:
                if res.status_code in [200, 201]:
                    return res.json().get('id')
                else:
                    raise Exception(f"Échec de la validation de fin d'upload OneDrive: {res.text}")
            start = end + 1


def download_from_onedrive(token, file_id, dest_path):
    """Téléchargement de fichier depuis OneDrive."""
    access_token = token
    if token.strip().startswith('{'):
        try:
            token_data = json.loads(token)
            access_token = token_data.get('access_token', token)
        except Exception:
            pass
            
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}/content"
    response = requests.get(url, headers=headers, stream=True)
    if response.status_code != 200:
        raise Exception(f"Échec du téléchargement OneDrive: {response.text}")
        
    with open(dest_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)


# --- MAIN ACTIONS ---

def _restore_postgresql_robust(project, dump_file_path, dump_file_name, logger_accum):
    """
    Restaure une base de données PostgreSQL de manière robuste en fermant les connexions
    et en épurant les directives incompatibles pour les versions de postgres inférieures.
    """
    host = project.db_host or "localhost"
    port = project.db_port or "5432"
    name = project.db_name
    user = project.db_user
    password = project.db_password
    
    psql_path = find_pg_binary('psql')
    if not psql_path:
        logger_accum.log("L'utilitaire psql est introuvable sur le système.")
        return False
        
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    env["PGCLIENTENCODING"] = "UTF8"
    env["USERNAME"] = user
    env["USER"] = user

    # 1. Fermeture forcée des connexions concurrentes
    logger_accum.log("Fermeture forcée des connexions concurrentes...")
    terminate_sql = f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{name}' AND pid <> pg_backend_pid();"
    terminate_cmd = [
        psql_path, "-h", str(host), "-p", str(port), "-U", str(user), "-d", "postgres", "-v", "ON_ERROR_STOP=0", "-c", terminate_sql
    ]
    try:
        subprocess.run(terminate_cmd, env=env, capture_output=True, text=True, timeout=20)
    except Exception as e:
        logger_accum.log(f"Avertissement (fermeture connexions) : {e}")

    import time
    time.sleep(1)

    # Si c'est un fichier binaire .dump, on doit utiliser pg_restore directement
    if dump_file_name.endswith('.dump'):
        logger_accum.log("Restauration du dump binaire via pg_restore...")
        pg_restore_path = find_pg_binary('pg_restore')
        if not pg_restore_path:
            logger_accum.log("L'utilitaire pg_restore est introuvable.")
            return False
            
        cmd = [
            pg_restore_path,
            '-h', str(host),
            '-p', str(port),
            '-U', str(user),
            '-d', str(name),
            '--clean',
            '--if-exists',
            '-v',
            dump_file_path
        ]
        
        process = subprocess.run(
            cmd, 
            env=env, 
            capture_output=True, 
            text=True, 
            encoding='utf-8', 
            errors='ignore',
            timeout=300
        )
        if process.returncode != 0:
            logger_accum.log(f"Erreur pg_restore (code {process.returncode}):\n{process.stderr}")
            return False
        return True

    # 2. Préparation du schéma de base propre (pour plain SQL)
    logger_accum.log("Réinitialisation du schéma public...")
    cleanup_sql = "SET client_encoding = 'UTF8';\nDROP SCHEMA public CASCADE;\nCREATE SCHEMA public;\nGRANT ALL ON SCHEMA public TO public;\n"

    # 3. Traitement et filtrage de sécurité du fichier SQL de dump (directives incompatibles)
    try:
        with open(dump_file_path, "rb") as f:
            backup_content = f.read()
    except Exception as e:
        logger_accum.log(f"Lecture du fichier dump impossible : {e}")
        return False

    try:
        backup_text = backup_content.decode("utf-8", errors="ignore")
        lines = backup_text.split("\n")
        cleaned_lines = []
        
        unsupported_params = [
            "transaction_timeout", "idle_session_timeout", 
            "idle_in_transaction_session_timeout", "wal_compression", "logical_decoding_work_mem"
        ]
        
        for line in lines:
            line_stripped = line.strip().upper()
            should_skip = False
            if line_stripped.startswith("SET "):
                for param in unsupported_params:
                    if f" {param.upper()} " in line_stripped or line_stripped.endswith(f" {param.upper()}"):
                        should_skip = True
                        break
            if not should_skip:
                cleaned_lines.append(line)
        cleaned_content = "\n".join(cleaned_lines).encode("utf-8")
    except Exception as e:
        logger_accum.log(f"Erreur lors du filtrage du dump : {e}. Utilisation du fichier d'origine.")
        cleaned_content = backup_content

    full_content = cleanup_sql.encode("utf-8") + b"\n" + cleaned_content

    # 4. Écriture du flux épuré
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".sql", delete=False) as tmp:
        tmp.write(full_content)
        tmp_path = tmp.name

    try:
        # 5. Injection via psql avec tolérance aux erreurs mineures
        logger_accum.log("Restauration du script SQL via psql...")
        cmd = [
            psql_path, "-h", str(host), "-p", str(port), "-U", str(user), "-d", str(name), "-v", "ON_ERROR_STOP=0", "-f", tmp_path
        ]
        
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
        )
        
        # 6. Évaluation stricte des erreurs critiques renvoyées
        combined_output = (result.stderr or "").lower() + "\n" + (result.stdout or "").lower()
        critical_errors = [
            "permission denied", "could not open file", "out of memory", 
            "disk full", "connection refused", "authentication failed", 
            "database does not exist", "role does not exist", "fe_sendauth"
        ]
        
        if any(err in combined_output for err in critical_errors):
            logger_accum.log(f"Erreur critique lors de la restauration :\n{result.stderr or result.stdout}")
            return False

        return True

    except subprocess.TimeoutExpired:
        logger_accum.log("Le délai d'attente pour la restauration SQL a expiré.")
        return False
    except Exception as e:
        logger_accum.log(f"Erreur fatale d'exécution : {str(e)}")
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def executer_sauvegarde_projet(projet_id):
    """Génère le dump DB + Zip Médias et distribue vers les destinations configurées."""
    logger_accum = LogAccumulator()
    logger_accum.log(f"Début de la procédure de sauvegarde pour le projet ID {projet_id}")
    
    historique = None
    temp_dir = None
    
    try:
        project = ProjetSauvegarde.objects.get(id=projet_id)
    except ProjetSauvegarde.DoesNotExist:
        logger_accum.log(f"Projet ID {projet_id} introuvable.")
        return None
        
    try:
        dump_filename = "db.sql"
        temp_dump_file = os.path.join(temp_dir, dump_filename)
        
        # 1. Génération du dump PostgreSQL
        logger_accum.log(f"Sauvegarde de la base de données '{project.db_name}'...")
        pg_dump_path = find_pg_binary('pg_dump')
        
        cmd = [
            pg_dump_path,
            '-h', project.db_host,
            '-p', project.db_port,
            '-U', project.db_user,
            '-F', 'p',  # Plain SQL format
            '--clean',
            '--if-exists',
            '--encoding=UTF8',
            '-b',
            '-v',
            '-f', temp_dump_file,
            project.db_name
        ]
        
        env = os.environ.copy()
        env['PGPASSWORD'] = project.db_password
        
        process = subprocess.run(
            cmd, 
            env=env, 
            capture_output=True, 
            text=True, 
            encoding='utf-8', 
            errors='ignore'
        )
        
        if process.returncode != 0:
            error_msg = f"Erreur pg_dump (code {process.returncode}):\n{process.stderr}"
            logger_accum.log(error_msg)
            raise Exception("pg_dump a échoué.")
            
        logger_accum.log("Base de données sauvegardée avec succès dans le fichier temporaire.")
        
        # 2. Création de l'archive ZIP
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        zip_filename = f"{project.nom_projet}_sauvegarde_{timestamp}.zip"
        temp_zip_path = os.path.join(temp_dir, zip_filename)
        
        logger_accum.log("Création de l'archive compressée ZIP...")
        with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(temp_dump_file, arcname=dump_filename)
            
            # Ajouter le dossier média local si spécifié et existant
            media_path = project.chemin_media_local
            if media_path and os.path.exists(media_path):
                logger_accum.log(f"Ajout du dossier média local '{media_path}' dans l'archive...")
                file_count = 0
                for root, dirs, files in os.walk(media_path):
                    for file in files:
                        full_file_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_file_path, media_path)
                        arcname = os.path.join('media', rel_path)
                        zipf.write(full_file_path, arcname=arcname)
                        file_count += 1
                logger_accum.log(f"{file_count} fichiers médias ajoutés.")
            else:
                logger_accum.log("Aucun dossier média valide trouvé à archiver.")
                
        # 3. Copie vers les destinations
        destinations = project.destinations.all()
        archive_paths_logged = []
        
        if not destinations.exists():
            # Sauvegarde locale par défaut dans le projet si aucune destination
            fallback_dir = os.path.join(settings.BASE_DIR, 'backups', project.nom_projet)
            os.makedirs(fallback_dir, exist_ok=True)
            fallback_path = os.path.join(fallback_dir, zip_filename)
            shutil.copy2(temp_zip_path, fallback_path)
            archive_paths_logged.append(fallback_path)
            logger_accum.log(f"Aucune destination spécifiée. Archivage par défaut local : {fallback_path}")
        else:
            for dest in destinations:
                logger_accum.log(f"Envoi vers la destination '{dest.type_destination}'...")
                try:
                    if dest.type_destination == 'local':
                        local_dir = dest.chemin_local
                        if not local_dir:
                            raise Exception("Le chemin de destination local n'est pas spécifié.")
                        os.makedirs(local_dir, exist_ok=True)
                        dest_file_path = os.path.join(local_dir, zip_filename)
                        shutil.copy2(temp_zip_path, dest_file_path)
                        archive_paths_logged.append(dest_file_path)
                        logger_accum.log(f"Sauvegarde réussie vers l'emplacement local : {dest_file_path}")
                        
                    elif dest.type_destination == 'gdrive':
                        if not dest.token_auth_cloud:
                            raise Exception("Le token Google Drive n'est pas configuré.")
                        folder_id = dest.dossier_cible_cloud or None
                        file_id = upload_to_gdrive(dest.token_auth_cloud, temp_zip_path, folder_id)
                        uri = f"gdrive://{file_id}"
                        archive_paths_logged.append(uri)
                        logger_accum.log(f"Sauvegarde réussie vers Google Drive (ID: {file_id})")
                        
                    elif dest.type_destination == 'onedrive':
                        if not dest.token_auth_cloud:
                            raise Exception("Le token OneDrive n'est pas configuré.")
                        folder_path = dest.dossier_cible_cloud or None
                        file_id = upload_to_onedrive(dest.token_auth_cloud, temp_zip_path, folder_path)
                        uri = f"onedrive://{file_id}"
                        archive_paths_logged.append(uri)
                        logger_accum.log(f"Sauvegarde réussie vers OneDrive (ID: {file_id})")
                        
                except Exception as ex:
                    logger_accum.log(f"Échec de l'envoi vers '{dest.type_destination}': {str(ex)}")
                    raise ex
        
        # Enregistrement de l'historique
        chemin_genere = archive_paths_logged[0] if archive_paths_logged else ""
        historique = HistoriqueSauvegarde.objects.create(
            projet=project,
            type_action='sauvegarde',
            statut='succes',
            message_log="",
            chemin_archive_generee=chemin_genere
        )
        logger_accum.log("Sauvegarde terminée avec succès.")
        historique.message_log = logger_accum.get_content()
        historique.save()
        
    except Exception as e:
        logger_accum.log(f"Erreur fatale lors de la sauvegarde : {str(e)}")
        if 'project' in locals():
            historique = HistoriqueSauvegarde.objects.create(
                projet=project,
                type_action='sauvegarde',
                statut='echec',
                message_log=logger_accum.get_content(),
                chemin_archive_generee=""
            )
            
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
                
    return historique


def executer_restoration_projet(historique_id):
    """Restaure une sauvegarde spécifique (Base DB + dossiers médias) à partir de son historique."""
    logger_accum = LogAccumulator()
    logger_accum.log(f"Début de la restauration à partir de l'historique ID {historique_id}")
    
    restore_history = None
    temp_dir = None
    
    try:
        historique = HistoriqueSauvegarde.objects.get(id=historique_id)
        project = historique.projet
    except HistoriqueSauvegarde.DoesNotExist:
        logger_accum.log(f"Historique ID {historique_id} introuvable.")
        return None
        
    try:
        # Enregistrer l'historique de la restauration courante
        restore_history = HistoriqueSauvegarde.objects.create(
            projet=project,
            type_action='restauration',
            statut='echec',  # Statut temporaire avant succès
            message_log="",
            chemin_archive_generee=historique.chemin_archive_generee
        )
        
        archive_uri = historique.chemin_archive_generee
        if not archive_uri:
            raise Exception("Aucune archive n'est associée à cet historique.")
            
        temp_dir = tempfile.mkdtemp()
        
        ext = os.path.splitext(archive_uri)[1].lower() if archive_uri else ".zip"
        if not ext:
            ext = ".zip"
            
        local_file_path = os.path.join(temp_dir, f"backup{ext}")
        
        # 1. Récupération de l'archive
        logger_accum.log(f"Récupération de l'archive depuis {archive_uri}...")
        
        if archive_uri.startswith("gdrive://"):
            file_id = archive_uri.replace("gdrive://", "")
            gdrive_dest = project.destinations.filter(type_destination='gdrive').first()
            if not gdrive_dest or not gdrive_dest.token_auth_cloud:
                raise Exception("Token Google Drive configuré requis pour télécharger le fichier.")
            download_from_gdrive(gdrive_dest.token_auth_cloud, file_id, local_file_path)
            logger_accum.log("Archive téléchargée de Google Drive avec succès.")
            
        elif archive_uri.startswith("onedrive://"):
            file_id = archive_uri.replace("onedrive://", "")
            onedrive_dest = project.destinations.filter(type_destination='onedrive').first()
            if not onedrive_dest or not onedrive_dest.token_auth_cloud:
                raise Exception("Token OneDrive configuré requis pour télécharger le fichier.")
            download_from_onedrive(onedrive_dest.token_auth_cloud, file_id, local_file_path)
            logger_accum.log("Archive téléchargée de OneDrive avec succès.")
            
        else:
            # Traiter comme un chemin local standard
            if not os.path.exists(archive_uri):
                raise Exception(f"L'archive locale spécifiée n'existe pas : {archive_uri}")
            shutil.copy2(archive_uri, local_file_path)
            logger_accum.log("Copie de l'archive locale terminée.")
            
        if ext == ".zip":
            # 2. Décompression
            extracted_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extracted_dir, exist_ok=True)
            logger_accum.log("Décompression de l'archive...")
            with zipfile.ZipFile(local_file_path, 'r') as zipf:
                zipf.extractall(extracted_dir)
                
            # Trouver le dump de la base
            dump_files = [f for f in os.listdir(extracted_dir) if f.endswith('.dump') or f.endswith('.sql')]
            if not dump_files:
                raise Exception("Aucun fichier dump (.dump/.sql) trouvé dans l'archive.")
                
            dump_file_name = dump_files[0]
            dump_file_path = os.path.join(extracted_dir, dump_file_name)
            logger_accum.log(f"Fichier de dump base détecté : {dump_file_name}")
            
            # 3. Lancement de la restauration PostgreSQL
            logger_accum.log(f"Restauration de la base '{project.db_name}'...")
            success = _restore_postgresql_robust(project, dump_file_path, dump_file_name, logger_accum)
            if not success:
                raise Exception("La restauration de la base de données a échoué.")
            logger_accum.log("Restauration de la base de données terminée avec succès.")
            
            # 4. Restauration du dossier média local
            extracted_media = os.path.join(extracted_dir, 'media')
            target_media = project.chemin_media_local
            
            if os.path.exists(extracted_media):
                logger_accum.log(f"Restauration du dossier média vers '{target_media}'...")
                
                # Vider proprement le dossier média cible
                if os.path.exists(target_media):
                    logger_accum.log("Nettoyage du répertoire média existant...")
                    for item in os.listdir(target_media):
                        item_path = os.path.join(target_media, item)
                        try:
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path)
                            else:
                                os.remove(item_path)
                        except Exception as err:
                            logger_accum.log(f"Avertissement lors de la suppression de {item_path} : {str(err)}")
                else:
                    os.makedirs(target_media, exist_ok=True)
                    
                # Recopier les fichiers extraits
                count = 0
                for item in os.listdir(extracted_media):
                    src_path = os.path.join(extracted_media, item)
                    dest_path = os.path.join(target_media, item)
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path)
                    else:
                        shutil.copy2(src_path, dest_path)
                    count += 1
                logger_accum.log(f"Restauration des médias terminée. {count} fichiers copiés.")
            else:
                logger_accum.log("Aucune sauvegarde de média présente dans l'archive. Restauration média sautée.")
        else:
            # SQL ou DUMP brut
            logger_accum.log(f"Restauration directe du dump brut ({ext})...")
            success = _restore_postgresql_robust(project, local_file_path, f"database{ext}", logger_accum)
            if not success:
                raise Exception("La restauration de la base de données a échoué.")
            logger_accum.log("Restauration de la base de données terminée avec succès. (Médias ignorés car dump brut)")
            
        logger_accum.log("Procédure de restauration globale terminée avec succès.")
        restore_history.statut = 'succes'
        restore_history.message_log = logger_accum.get_content()
        restore_history.save()
        
    except Exception as e:
        logger_accum.log(f"Erreur fatale lors de la restauration : {str(e)}")
        if restore_history:
            restore_history.statut = 'echec'
            restore_history.message_log = logger_accum.get_content()
            restore_history.save()
            
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
                
    return restore_history


def executer_restoration_projet_depuis_fichier(projet_id, zip_file_path):
    """Restaure une base de données et des médias à partir d'un fichier ZIP spécifié directement."""
    logger_accum = LogAccumulator()
    logger_accum.log(f"Début de la restauration à partir du fichier direct: {zip_file_path}")
    
    restore_history = None
    temp_dir = None
    
    try:
        project = ProjetSauvegarde.objects.get(id=projet_id)
    except ProjetSauvegarde.DoesNotExist:
        logger_accum.log(f"Projet ID {projet_id} introuvable.")
        return None
        
    try:
        # Enregistrer l'historique de la restauration courante
        restore_history = HistoriqueSauvegarde.objects.create(
            projet=project,
            type_action='restauration',
            statut='echec',
            message_log="",
            chemin_archive_generee=zip_file_path
        )
        
        if not os.path.exists(zip_file_path):
            raise Exception(f"Le fichier d'archive spécifié n'existe pas: {zip_file_path}")
            
        temp_dir = tempfile.mkdtemp()
        
        ext = os.path.splitext(zip_file_path)[1].lower() if zip_file_path else ".zip"
        if not ext:
            ext = ".zip"
            
        local_file_path = os.path.join(temp_dir, f"backup{ext}")
        
        # Copier vers le dossier temporaire
        shutil.copy2(zip_file_path, local_file_path)
        logger_accum.log("Fichier copié vers l'espace temporaire.")
        
        if ext == ".zip":
            # Décompresser
            extracted_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(extracted_dir, exist_ok=True)
            logger_accum.log("Décompression de l'archive...")
            with zipfile.ZipFile(local_file_path, 'r') as zipf:
                zipf.extractall(extracted_dir)
                
            # Trouver le dump
            dump_files = [f for f in os.listdir(extracted_dir) if f.endswith('.dump') or f.endswith('.sql')]
            if not dump_files:
                raise Exception("Aucun fichier dump (.dump/.sql) trouvé dans l'archive.")
                
            dump_file_name = dump_files[0]
            dump_file_path = os.path.join(extracted_dir, dump_file_name)
            logger_accum.log(f"Fichier de dump base détecté : {dump_file_name}")
            
            # Restauration DB
            logger_accum.log(f"Restauration de la base '{project.db_name}'...")
            success = _restore_postgresql_robust(project, dump_file_path, dump_file_name, logger_accum)
            if not success:
                raise Exception("La restauration de la base de données a échoué.")
            logger_accum.log("Restauration de la base de données terminée avec succès.")
            
            # Restauration Médias
            extracted_media = os.path.join(extracted_dir, 'media')
            target_media = project.chemin_media_local
            
            if os.path.exists(extracted_media):
                logger_accum.log(f"Restauration du dossier média vers '{target_media}'...")
                if os.path.exists(target_media):
                    logger_accum.log("Nettoyage du répertoire média existant...")
                    for item in os.listdir(target_media):
                        item_path = os.path.join(target_media, item)
                        try:
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path)
                            else:
                                os.remove(item_path)
                        except Exception as err:
                            logger_accum.log(f"Avertissement lors de la suppression de {item_path} : {str(err)}")
                else:
                    os.makedirs(target_media, exist_ok=True)
                    
                count = 0
                for item in os.listdir(extracted_media):
                    src_path = os.path.join(extracted_media, item)
                    dest_path = os.path.join(target_media, item)
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path)
                    else:
                        shutil.copy2(src_path, dest_path)
                    count += 1
                logger_accum.log(f"Restauration des médias terminée. {count} fichiers copiés.")
            else:
                logger_accum.log("Aucune sauvegarde de média présente dans l'archive. Restauration média sautée.")
        else:
            # SQL ou DUMP brut
            logger_accum.log(f"Restauration directe du dump brut ({ext})...")
            success = _restore_postgresql_robust(project, local_file_path, f"database{ext}", logger_accum)
            if not success:
                raise Exception("La restauration de la base de données a échoué.")
            logger_accum.log("Restauration de la base de données terminée avec succès. (Médias ignorés car dump brut)")
            
        logger_accum.log("Procédure de restauration globale terminée avec succès.")
        restore_history.statut = 'succes'
        restore_history.message_log = logger_accum.get_content()
        restore_history.save()
        
    except Exception as e:
        logger_accum.log(f"Erreur fatale lors de la restauration : {str(e)}")
        if restore_history:
            restore_history.statut = 'echec'
            restore_history.message_log = logger_accum.get_content()
            restore_history.save()
            
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
                
    return restore_history

