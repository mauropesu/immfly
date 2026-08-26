#!/bin/bash

# Check if the script is being run as root
if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run this script as root (e.g., sudo ./update_ovpn_auth.sh)."
  exit 1
fi

# 1 and 2. Ask for the first and second line (username and password)
read -p "Enter the new username: " new_username

# Use the -s option so the password is not displayed on the screen while typing
read -s -p "Enter the new password: " new_password
echo "" # Just to add a newline in the console

# Define file paths
FILE1="/etc/openvpn/30_auth.ovpn"
FILE2="/etc/openvpn/31_auth.ovpn"

# 3. Replace existing information in the files
# The '>' symbol overwrites the file with line 1.
# The '>>' symbol appends line 2 to the end of the file.

echo "$new_username" >"$FILE1"
echo "$new_password" >>"$FILE1"

echo "$new_username" >"$FILE2"
echo "$new_password" >>"$FILE2"

echo "Credentials have been updated in $FILE1 and $FILE2."

# 4. Restart the OpenVPN service
echo "Restarting OpenVPN service..."
systemctl restart openvpn

# Quick verification of the restart
if [ $? -eq 0 ]; then
  echo "OpenVPN service restarted successfully."
else
  echo "Warning: There was an error trying to restart the OpenVPN service."
fi
