#!/bin/bash

# Configuración
HOST="192.168.150.203"
USER="root" # Modificar si utilizas otro usuario
PASS="pepajerusalembecarier.."
FILE="/etc/config/wireless"
BACKUP="/etc/config/wireless.bak"

run_ssh() {
  sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no "${USER}@${HOST}" "$1"
}

# 1. Crear respaldo remoto del archivo original (solo si no existe)
run_ssh "[ ! -f '$BACKUP' ] && cp '$FILE' '$BACKUP'" 2>/dev/null

# 2. Opción para restaurar
if [ "$1" == "restore" ]; then
  echo "Restaurando la configuración original..."
  run_ssh "cp '$BACKUP' '$FILE' && wifi reload 2>/dev/null || true"
  echo "Configuración original restaurada exitosamente."
  exit 0
fi

# 3. Obtener el nuevo SSID (por argumento o por prompt)
NEW_SSID="$1"
if [ -z "$NEW_SSID" ]; then
  read -rp "Ingresa el nuevo nombre de SSID: " NEW_SSID
fi

if [ -z "$NEW_SSID" ]; then
  echo "Error: No se ingresó ningún SSID."
  exit 1
fi

# 4. Aplicar el cambio de SSID
echo "Cambiando SSID a '$NEW_SSID' en $HOST..."
run_ssh "sed -i -E \"s/(option ssid ')[^']*/\1$NEW_SSID/g\" '$FILE' && wifi reload 2>/dev/null || true"

if [ $? -eq 0 ]; then
  echo "SSID cambiado a '$NEW_SSID' exitosamente."
else
  echo "Error al conectar o aplicar la configuración."
fi
