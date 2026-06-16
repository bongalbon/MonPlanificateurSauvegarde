const { app, BrowserWindow, Menu, Tray, ipcMain, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const fs = require('fs');

let mainWindow;
let tray = null;
let djangoProcess = null;
let logQueue = [];

const isHidden = process.argv.includes('--hidden');

// Envoyer un message de journalisation au Renderer
function sendAppLog(message, type = 'info') {
    const logItem = {
        timestamp: new Date().toISOString(),
        message: message.trim(),
        type: type
    };
    logQueue.push(logItem);
    if (logQueue.length > 500) {
        logQueue.shift();
    }
    
    if (mainWindow && mainWindow.webContents) {
        try {
            mainWindow.webContents.send('app-log', logItem);
        } catch (e) {
            // Fenêtre pas encore complètement disponible ou déjà détruite
        }
    }
}

// Handler IPC synchrone pour renvoyer la file de logs au démarrage du Renderer
ipcMain.on('request-logs', (event) => {
    event.returnValue = logQueue;
});

// Configurer le démarrage automatique avec Windows (en arrière-plan dans le systray)
function setupAutostart() {
    if (app.isPackaged && process.platform === 'win32') {
        app.setLoginItemSettings({
            openAtLogin: true,
            path: app.getPath('exe'),
            args: ['--hidden']
        });
        sendAppLog("Configuration du démarrage Windows (mode Systray) configurée.", "info");
    } else {
        sendAppLog("Démarrage automatique ignoré en mode de développement.", "info");
    }
}

// Lancer le backend Django
function spawnDjango() {
    const pythonPath = path.join(__dirname, '..', 'venv', 'Scripts', 'python.exe');
    const managePyPath = path.join(__dirname, '..', 'manage.py');
    
    const exePath = fs.existsSync(pythonPath) ? pythonPath : 'python';
    
    sendAppLog(`Tentative de démarrage de Django avec : ${exePath} (manage.py = ${managePyPath})`, "info");
    
    djangoProcess = spawn(exePath, [managePyPath, 'runserver', '127.0.0.1:8000', '--noreload'], {
        windowsHide: true
    });
    
    djangoProcess.stdout.on('data', (data) => {
        const text = data.toString();
        console.log(`Django stdout: ${text}`);
        sendAppLog(`[Django] ${text}`, 'django');
    });
    
    djangoProcess.stderr.on('data', (data) => {
        const text = data.toString();
        console.error(`Django stderr: ${text}`);
        sendAppLog(`[Django STDERR] ${text}`, 'error');
    });

    djangoProcess.on('error', (err) => {
        console.error('Failed to start Django process.', err);
        sendAppLog(`Échec critique du lancement de Django : ${err.message}`, 'error');
    });

    djangoProcess.on('exit', (code, signal) => {
        console.log(`Django process exited with code ${code} and signal ${signal}`);
        sendAppLog(`Le processus Django s'est arrêté (Code: ${code}, Signal: ${signal})`, 'warn');
    });
}

// Récupérer la liste des projets configurés depuis l'API
function fetchProjects(callback) {
    http.get('http://127.0.0.1:8000/api/projet/', (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
            try {
                if (res.statusCode === 200) {
                    const projects = JSON.parse(data);
                    callback(null, projects);
                } else {
                    callback(new Error(`Status: ${res.statusCode}`), null);
                }
            } catch (e) {
                callback(e, null);
            }
        });
    }).on('error', (err) => {
        callback(err, null);
    });
}

// Lancer la sauvegarde à distance depuis le Systray
function triggerBackup(projectId, projectName) {
    const options = {
        hostname: '127.0.0.1',
        port: 8000,
        path: `/api/projet/${projectId}/sauvegarder/`,
        method: 'POST',
        headers: {
            'Content-Length': '0'
        }
    };
    
    const req = http.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
            if (res.statusCode === 200) {
                dialog.showMessageBox(mainWindow, {
                    type: 'info',
                    title: 'Sauvegarde Réussie',
                    message: `La sauvegarde pour le projet "${projectName}" a été effectuée avec succès.`
                });
            } else {
                dialog.showMessageBox(mainWindow, {
                    type: 'error',
                    title: 'Échec de la Sauvegarde',
                    message: `La sauvegarde pour le projet "${projectName}" a échoué.\nConsultez l'historique dans l'application pour plus de détails.`
                });
            }
        });
    });
    
    req.on('error', (err) => {
        dialog.showMessageBox(mainWindow, {
            type: 'error',
            title: 'Erreur Réseau',
            message: `Impossible de se connecter au backend Django : ${err.message}`
        });
    });
    
    req.end();
}

// Mettre à jour dynamiquement le menu contextuel du Systray
function updateTrayMenu() {
    if (!tray) return;
    
    fetchProjects((err, projects) => {
        let submenuItems = [];
        if (err || !projects || !Array.isArray(projects) || projects.length === 0) {
            submenuItems = [{ label: 'Aucun projet configuré', enabled: false }];
        } else {
            submenuItems = projects.map(project => ({
                label: project.nom_projet,
                click: () => {
                    triggerBackup(project.id, project.nom_projet);
                }
            }));
        }
        
        const contextMenu = Menu.buildFromTemplate([
            {
                label: 'Ouvrir Mon Planificateur',
                click: () => {
                    if (mainWindow) {
                        mainWindow.show();
                        mainWindow.focus();
                    }
                }
            },
            { type: 'separator' },
            {
                label: 'Sauvegarder un projet',
                submenu: submenuItems
            },
            { type: 'separator' },
            {
                label: 'Quitter',
                click: () => {
                    app.isQuiting = true;
                    app.quit();
                }
            }
        ]);
        
        tray.setContextMenu(contextMenu);
        tray.setToolTip('Mon Planificateur de Sauvegarde');
    });
}

// Créer le Systray
function createTray() {
    let iconPath = path.join(__dirname, 'build', 'icon.ico');
    if (process.platform !== 'win32' || !fs.existsSync(iconPath)) {
        iconPath = path.join(__dirname, 'build', 'icon.png');
    }
    
    if (fs.existsSync(iconPath)) {
        try {
            tray = new Tray(iconPath);
            sendAppLog(`Icône Systray chargée depuis ${path.basename(iconPath)}`, "info");
        } catch (e) {
            console.error("Erreur chargement icône systray:", e);
            const { nativeImage } = require('electron');
            tray = new Tray(nativeImage.createEmpty());
        }
    } else {
        const { nativeImage } = require('electron');
        tray = new Tray(nativeImage.createEmpty());
        sendAppLog("Icône Systray non trouvée. Utilisation d'une icône vide.", "warn");
    }
    
    updateTrayMenu();
    
    // Mettre à jour la liste des projets toutes les 15 secondes
    setInterval(updateTrayMenu, 15000);
}

// Créer la fenêtre principale
function createWindow() {
    let iconPath = path.join(__dirname, 'build', 'icon.ico');
    if (process.platform !== 'win32' || !fs.existsSync(iconPath)) {
        iconPath = path.join(__dirname, 'build', 'icon.png');
    }
    const hasIcon = fs.existsSync(iconPath);
    
    mainWindow = new BrowserWindow({
        width: 1200,
        height: 800,
        minWidth: 1000,
        minHeight: 700,
        icon: hasIcon ? iconPath : undefined,
        webPreferences: {
            nodeIntegration: true,
            contextIsolation: false
        },
        show: false
    });
    
    mainWindow.loadFile('index.html');
    
    mainWindow.once('ready-to-show', () => {
        const isHiddenAtStart = process.argv.includes('--hidden') || 
                                process.argv.includes('--open-as-hidden') || 
                                (app.isPackaged && app.getLoginItemSettings().wasOpenedAsHidden);
        if (!isHiddenAtStart) {
            mainWindow.show();
        } else {
            sendAppLog("Démarrage en mode caché (arrière-plan).", "info");
        }
    });
    
    // Intercepter le clic sur le bouton de fermeture "X"
    mainWindow.on('close', (event) => {
        if (!app.isQuiting) {
            event.preventDefault();
            mainWindow.hide();
        }
        return false;
    });
}

app.on('ready', () => {
    spawnDjango();
    setupAutostart();
    createWindow();
    // Laisser au backend Django le temps de démarrer avant d'appeler l'API pour le plateau système
    setTimeout(createTray, 2000);
});

app.on('will-quit', () => {
    if (djangoProcess) {
        djangoProcess.kill();
    }
});

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        app.quit();
    }
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
    }
});

// IPC : Sélecteurs de répertoires natifs
ipcMain.on('select-media-folder', (event) => {
    dialog.showOpenDialog({
        title: 'Sélectionner le dossier des médias locaux',
        properties: ['openDirectory']
    }).then(result => {
        if (!result.canceled && result.filePaths.length > 0) {
            sendAppLog(`Dossier médias locaux sélectionné : ${result.filePaths[0]}`, 'info');
            event.sender.send('media-folder-selected', result.filePaths[0]);
        }
    });
});

ipcMain.on('select-backup-folder', (event) => {
    dialog.showOpenDialog({
        title: 'Sélectionner le dossier de sauvegarde local',
        properties: ['openDirectory']
    }).then(result => {
        if (!result.canceled && result.filePaths.length > 0) {
            sendAppLog(`Dossier de sauvegarde local sélectionné : ${result.filePaths[0]}`, 'info');
            event.sender.send('backup-folder-selected', result.filePaths[0]);
        }
    });
});

ipcMain.on('select-backup-file', (event) => {
    dialog.showOpenDialog({
        title: 'Sélectionner une archive de sauvegarde (.zip)',
        filters: [{ name: 'Archives de sauvegarde', extensions: ['zip'] }],
        properties: ['openFile']
    }).then(result => {
        if (!result.canceled && result.filePaths.length > 0) {
            sendAppLog(`Archive de sauvegarde ZIP sélectionnée : ${result.filePaths[0]}`, 'info');
            event.sender.send('backup-file-selected', result.filePaths[0]);
        }
    });
});

let oauthServer = null;

ipcMain.on('start-oauth-flow', (event, provider) => {
    const http = require('http');
    const url = require('url');

    sendAppLog(`Lancement du flux OAuth pour : ${provider}`, 'info');

    if (oauthServer) {
        try {
            oauthServer.close();
        } catch (e) {}
    }

    oauthServer = http.createServer((req, res) => {
        const parsedUrl = url.parse(req.url, true);
        
        if (parsedUrl.pathname === '/authorize') {
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            
            const isGdrive = provider === 'gdrive';
            const title = isGdrive ? 'Google Drive' : 'OneDrive';
            const brandColor = isGdrive ? '#4285F4' : '#0078d4';
            const scopes = isGdrive 
                ? '<li>Accéder à vos fichiers d\'archives de sauvegarde</li><li>Créer et modifier des fichiers compressés (.zip)</li>'
                : '<li>Lire et écrire dans le dossier de sauvegarde dédié</li><li>Accéder hors connexion</li>';

            res.end(`
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Connexion à ${title}</title>
                    <style>
                        body {
                            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                            background-color: #0b0f19;
                            color: #f8fafc;
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            height: 100vh;
                            margin: 0;
                        }
                        .container {
                            background-color: #161f30;
                            border: 1px solid rgba(51, 65, 85, 0.45);
                            border-radius: 12px;
                            padding: 30px;
                            max-width: 440px;
                            width: 90%;
                            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
                            text-align: center;
                        }
                        .logo {
                            font-size: 24px;
                            font-weight: bold;
                            color: ${brandColor};
                            margin-bottom: 20px;
                        }
                        h3 {
                            margin-bottom: 12px;
                        }
                        p {
                            color: #94a3b8;
                            font-size: 14px;
                            line-height: 1.5;
                        }
                        ul {
                            text-align: left;
                            font-size: 13px;
                            color: #cbd5e1;
                            margin: 20px 0;
                            padding-left: 20px;
                        }
                        li {
                            margin-bottom: 8px;
                        }
                        .btn {
                            background-color: ${brandColor};
                            color: white;
                            border: none;
                            padding: 12px 24px;
                            border-radius: 6px;
                            font-size: 14px;
                            font-weight: 600;
                            cursor: pointer;
                            width: 100%;
                            transition: opacity 0.2s;
                        }
                        .btn:hover {
                            opacity: 0.9;
                        }
                        .btn-cancel {
                            background-color: transparent;
                            border: 1px solid rgba(255, 255, 255, 0.15);
                            color: #94a3b8;
                            margin-top: 10px;
                        }
                        .btn-cancel:hover {
                            background-color: rgba(255, 255, 255, 0.05);
                            color: white;
                        }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="logo">${title} Authentication</div>
                        <h3>Demande d'autorisation d'accès</h3>
                        <p>MonPlanificateurSauvegarde souhaite se connecter à votre compte afin de sauvegarder vos archives de base de données.</p>
                        <ul>
                            ${scopes}
                        </ul>
                        <button class="btn" onclick="location.href='/callback?code=mock_oauth_code_8085'">Autoriser l'accès</button>
                        <button class="btn btn-cancel" onclick="window.close()">Annuler</button>
                    </div>
                </body>
                </html>
            `);
        } else if (parsedUrl.pathname === '/callback') {
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            res.end(`
                <html>
                <body style="font-family: sans-serif; text-align: center; background-color: #0b0f19; color: white; padding-top: 100px;">
                    <h2 style="color: #10b981;">Connexion Réussie !</h2>
                    <p style="color: #94a3b8; margin-top: 10px;">L'application a capturé vos identifiants d'accès.</p>
                    <p style="color: #64748b; font-size: 14px;">Vous pouvez fermer cette fenêtre et retourner sur le panneau de configuration.</p>
                    <script>setTimeout(() => window.close(), 1200);</script>
                </body>
                </html>
            `);
            
            const mockToken = {
                access_token: `mock_access_token_${provider}_` + Math.random().toString(36).substring(7),
                refresh_token: `mock_refresh_token_${provider}_` + Math.random().toString(36).substring(7),
                expiry_date: Date.now() + 3600 * 1000
            };
            const tokenStr = JSON.stringify(mockToken, null, 2);
            
            sendAppLog(`Flux OAuth complété avec succès pour le fournisseur "${provider}". Jeton d'accès capturé.`, 'success');
            event.sender.send('oauth-flow-completed', provider, tokenStr);
            
            setTimeout(() => {
                if (oauthServer) {
                    oauthServer.close();
                    oauthServer = null;
                }
            }, 2000);
        }
    });

    oauthServer.listen(8085, () => {
        sendAppLog(`Serveur OAuth local d'écoute démarré sur le port 8085. Ouverture de la fenêtre d'authentification.`, 'info');
        const authWindow = new BrowserWindow({
            width: 480,
            height: 600,
            modal: true,
            parent: mainWindow,
            webPreferences: {
                nodeIntegration: false,
                contextIsolation: true
            }
        });
        authWindow.setMenuBarVisibility(false);
        authWindow.loadURL('http://localhost:8085/authorize');
    });
});

