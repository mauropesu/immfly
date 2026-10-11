#!/bin/bash

# Salir inmediatamente si un comando falla
set -e

# Función para verificar si un comando está instalado
is_installed() {
    command -v "$1" &> /dev/null
}

# Detectar Distribución
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    case "$OS" in
        fedora) OS_FAMILY="fedora" ;;
        ubuntu|debian|linuxmint|lmde|pop) OS_FAMILY="debian" ;;
        manjaro|arch) OS_FAMILY="arch" ;;
        *) echo "Distribución no soportada directamente"; exit 1 ;;
    esac
else
    echo "No se detectó un sistema Linux compatible. Saliendo..."; exit 1
fi

echo "Iniciando instalación automatizada en familia: $OS_FAMILY"

# ==========================================
# 1. INSTALACIÓN DE PAQUETES Y APLICACIONES
# ==========================================
echo "Instalando paquetes base y aplicaciones de terceros..."

if [ "$OS_FAMILY" == "fedora" ]; then
    sudo dnf update -y
    sudo dnf install -y zsh neovim git bat stow minicom eza gcc ripgrep fd-find wget curl unzip fontconfig
    
    # Tema Papirus y papirus-folders
    sudo dnf install -y libreoffice-icon-theme-papirus.x86_64 papirus-icon-theme-dark.noarch papirus-icon-theme.noarch papirus-folders

    # 1Password
    if ! is_installed 1password; then
        sudo rpm --import https://downloads.1password.com/linux/keys/1password.asc
        sudo sh -c 'echo -e "[1password]\nname=1Password\nbaseurl=https://downloads.1password.com/linux/rpm/stable/\$basearch\nenabled=1\ngpgcheck=1\nrepo_gpgcheck=1\ngpgkey=\"https://downloads.1password.com/linux/keys/1password.asc\"" > /etc/yum.repos.d/1password.repo'
        sudo dnf install -y 1password
    else
        echo "1Password ya está instalado. Omitiendo..."
    fi

elif [ "$OS_FAMILY" == "debian" ]; then
    sudo apt update
    sudo apt install -y zsh neovim git bat stow minicom gcc ripgrep fd-find wget curl unzip gpg flatpak papirus-icon-theme papirus-folders fontconfig

    # 1Password (Evitar duplicidad comprobando si ya existe el source o está instalado)
    if ! is_installed 1password; then
        if [ ! -f /etc/apt/sources.list.d/1password.sources ] && [ ! -f /etc/apt/sources.list.d/1password.list ]; then
            curl -sS https://downloads.1password.com/linux/keys/1password.asc | sudo gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg
            echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main' | sudo tee /etc/apt/sources.list.d/1password.list
        fi
        sudo apt update
        sudo apt install -y 1password
    else
        echo "1Password ya está instalado. Omitiendo..."
    fi

    # Mattermost
    if ! is_installed mattermost-desktop; then
        if [ ! -f /etc/apt/sources.list.d/mattermost.list ]; then
            curl -o- https://deb.packages.mattermost.com/setup-repo.sh | sudo bash
        fi
        sudo apt update
        sudo apt install -y mattermost-desktop
    else
        echo "Mattermost ya está instalado. Omitiendo..."
    fi

    # Google Chrome
    if ! is_installed google-chrome && ! is_installed google-chrome-stable; then
        wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
        sudo apt install -y /tmp/chrome.deb && rm /tmp/chrome.deb
    else
        echo "Google Chrome ya está instalado. Omitiendo..."
    fi

    # Warp Terminal
    if ! is_installed warp-terminal; then
        curl -L "https://app.warp.dev/download?package=deb" -o /tmp/warp.deb
        sudo apt install -y /tmp/warp.deb && rm /tmp/warp.deb
    else
        echo "Warp Terminal ya está instalado. Omitiendo..."
    fi

    # Ghostty
    if ! is_installed ghostty; then
        echo "Instalando Ghostty..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/mkasberg/ghostty-ubuntu/HEAD/install.sh)"
    else
        echo "Ghostty ya está instalado. Omitiendo..."
    fi
fi

# ==========================================
# 2. CONFIGURACIÓN DE PAPIRUS ICON THEME
# ==========================================
echo "Configurando Papirus Icon Theme..."
if is_installed papirus-folders; then
    # Usamos sudo porque los íconos de Papirus generalmente se instalan en /usr/share/icons
    sudo papirus-folders -C teal --theme Papirus-Dark
else
    echo "Advertencia: papirus-folders no está instalado. No se pudo aplicar el color teal."
fi

# ==========================================
# 3. DESCARGA E INSTALACIÓN DE FUENTES
# ==========================================
echo "Configurando fuentes (Meslo y Comic Code)..."
FONT_DIR="$HOME/.local/share/fonts"
mkdir -p "$FONT_DIR"

# MesloLG Nerd Font
if [ ! -d "$FONT_DIR/Meslo" ]; then
    echo "Descargando MesloLG Nerd Font..."
    wget -q https://github.com/ryanoasis/nerd-fonts/releases/download/v3.4.0/Meslo.zip -O /tmp/Meslo.zip
    mkdir -p "$FONT_DIR/Meslo"
    unzip -q /tmp/Meslo.zip -d "$FONT_DIR/Meslo"
    rm /tmp/Meslo.zip
else
    echo "Meslo Nerd Font ya existe. Omitiendo..."
fi

# Comic Code Font
if [ ! -d "$FONT_DIR/ComicCode" ]; then
    echo "Descargando Comic Code Font..."
    wget -q https://ifonts.xyz/wp-content/uploads/2023/03/coco.zip -O /tmp/coco.zip
    mkdir -p "$FONT_DIR/ComicCode"
    unzip -q /tmp/coco.zip -d "$FONT_DIR/ComicCode"
    rm /tmp/coco.zip
else
    echo "Comic Code Font ya existe. Omitiendo..."
fi

# Actualizar la caché de fuentes del sistema
echo "Actualizando caché de fuentes..."
fc-cache -fv

# ==========================================
# 4. INSTALACIÓN DE WINBOX
# ==========================================
if ! is_installed winbox && [ ! -f ~/.local/bin/winbox ]; then
    echo "Instalando Winbox..."
    mkdir -p ~/.local/bin
    wget -q https://mt.lv/winbox64 -O ~/.local/bin/winbox
    chmod +x ~/.local/bin/winbox
else
    echo "Winbox ya está instalado. Omitiendo..."
fi

# ==========================================
# 5. ZSH PLUGINS Y TEMAS
# ==========================================
echo "Configurando ZSH..."
ZSH_DIR="${ZSH_DIR:-$HOME/.oh-my-zsh}"

if [ ! -d "$ZSH_DIR" ]; then
    echo "Instalando Oh My Zsh..."
    RUNZSH=no sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"
fi

declare -A PLUGINS=(
    ["zsh-syntax-highlighting"]="https://github.com/zsh-users/zsh-syntax-highlighting.git"
    ["zsh-autosuggestions"]="https://github.com/zsh-users/zsh-autosuggestions.git"
    ["zsh-completions"]="https://github.com/zsh-users/zsh-completions.git"
)

for plugin in "${!PLUGINS[@]}"; do
    if [ ! -d "$ZSH_DIR/plugins/$plugin" ]; then
        git clone "${PLUGINS[$plugin]}" "$ZSH_DIR/plugins/$plugin"
    else
        echo "Plugin $plugin ya existe. Omitiendo..."
    fi
done

if [ ! -d "$ZSH_DIR/themes/powerlevel10k" ]; then
    git clone --depth=1 https://github.com/romkatv/powerlevel10k.git "$ZSH_DIR/themes/powerlevel10k"
else
    echo "Tema powerlevel10k ya existe. Omitiendo..."
fi

# ==========================================
# 6. APLICACIÓN DE DOTFILES CON STOW
# ==========================================
echo "Aplicando enlaces simbólicos con Stow..."
cd ~/dotfiles || { echo "Directorio dotfiles no encontrado"; exit 1; }
mkdir -p ~/.config ~/.local/share/warp-terminal/themes

stow zsh ghostty nvim warp-linux || echo "Advertencia: Algunos paquetes de stow ya están enlazados."

# ==========================================
# 7. CONFIGURACIÓN DEL ENTORNO Y SHELL
# ==========================================
# Asegurar que ~/.local/bin (para Winbox) esté en el PATH
if [ -f ~/dotfiles/zsh/.zshrc ] && ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' ~/dotfiles/zsh/.zshrc; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/dotfiles/zsh/.zshrc
fi

# Cambiar la shell por defecto a ZSH
if [ "$SHELL" != "$(which zsh)" ]; then
    echo "Cambiando la shell por defecto a ZSH..."
    chsh -s "$(which zsh)"
fi

echo "¡Instalación completada con éxito!"
