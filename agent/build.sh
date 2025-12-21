#!/bin/bash

# Скрипт для сборки агента и создания DEB пакета

set -e

echo "Building backup-server-agent..."

# Проверка версии Go
GO_VERSION=$(go version | awk '{print $3}' | sed 's/go//')
REQUIRED_VERSION="1.16"

echo "Checking Go version..."
if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$GO_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "ERROR: Go version $GO_VERSION is too old. Required: Go $REQUIRED_VERSION or higher."
    echo "Current Go version: $(go version)"
    echo "Please upgrade Go to version 1.16 or higher."
    exit 1
fi

echo "Go version check passed: $(go version)"

# Загрузка зависимостей и обновление go.sum
echo "Downloading dependencies..."
go mod download
go mod tidy

# Сборка бинарника
echo "Building binary..."
go build -o backup-server-agent main.go

echo "Creating DEB package structure..."

# Создаем структуру DEB пакета
mkdir -p deb/usr/bin
mkdir -p deb/etc/backupserveragent
mkdir -p deb/DEBIAN

# Устанавливаем правильные права доступа для директорий
chmod 755 deb
chmod 755 deb/usr
chmod 755 deb/usr/bin
chmod 755 deb/etc
chmod 755 deb/etc/backupserveragent
chmod 755 deb/DEBIAN

# Проверяем, что бинарник существует
if [ ! -f "backup-server-agent" ]; then
    echo "ERROR: backup-server-agent binary not found!"
    exit 1
fi

# Копируем бинарник
cp backup-server-agent deb/usr/bin/

# Делаем исполняемым
chmod +x deb/usr/bin/backup-server-agent

# Проверяем, что файл скопирован
if [ ! -f "deb/usr/bin/backup-server-agent" ]; then
    echo "ERROR: Failed to copy backup-server-agent to deb/usr/bin/"
    exit 1
fi

echo "Binary copied: deb/usr/bin/backup-server-agent ($(stat -c%s deb/usr/bin/backup-server-agent 2>/dev/null || stat -f%z deb/usr/bin/backup-server-agent 2>/dev/null || echo "unknown") bytes)"

# Создаем пустой конфиг
mkdir -p deb/etc/backupserveragent
cat > deb/etc/backupserveragent/config.json <<'CONFIGEOF'
{
  "port": 11540,
  "server_ip": "",
  "server_hostname": "",
  "tasks": []
}
CONFIGEOF
chmod 644 deb/etc/backupserveragent/config.json

# Проверяем, что файл создан
if [ ! -f "deb/etc/backupserveragent/config.json" ]; then
    echo "ERROR: Failed to create config.json"
    exit 1
fi

echo "Config file created: deb/etc/backupserveragent/config.json"

# Устанавливаем правильные права доступа для DEBIAN
chmod 755 deb/DEBIAN
chmod 644 deb/DEBIAN/control

# Убеждаемся, что скрипты существуют и имеют правильный формат
for script in postinst postrm; do
    if [ ! -f "deb/DEBIAN/$script" ]; then
        echo "ERROR: deb/DEBIAN/$script not found!"
        exit 1
    fi
    
    # Создаем временный файл с правильным форматом
    temp_file=$(mktemp)
    
    # Читаем исходный файл, удаляем Windows line endings и добавляем перевод строки в конце
    tr -d '\r' < "deb/DEBIAN/$script" > "$temp_file"
    # Убеждаемся, что файл заканчивается переводом строки
    if [ -s "$temp_file" ] && [ "$(tail -c 1 "$temp_file")" != "" ]; then
        echo "" >> "$temp_file"
    fi
    
    # Копируем обратно
    cp "$temp_file" "deb/DEBIAN/$script"
    rm -f "$temp_file"
    
    # Устанавливаем права доступа
    chmod 755 "deb/DEBIAN/$script"
    
    # Проверяем, что shebang правильный
    first_line=$(head -n 1 "deb/DEBIAN/$script")
    if ! echo "$first_line" | grep -q "^#!/bin/bash"; then
        echo "ERROR: $script has incorrect shebang: $first_line"
        exit 1
    fi
    
    # Проверяем, что файл исполняемый
    if [ ! -x "deb/DEBIAN/$script" ]; then
        echo "ERROR: $script is not executable!"
        exit 1
    fi
    
    echo "Verified: deb/DEBIAN/$script (size: $(stat -c%s "deb/DEBIAN/$script" 2>/dev/null || stat -f%z "deb/DEBIAN/$script" 2>/dev/null || echo "unknown") bytes)"
done

# Проверяем, что все файлы в DEBIAN существуют и имеют правильные права
echo "DEBIAN directory contents:"
ls -la deb/DEBIAN/

# Проверяем, что control файл существует
if [ ! -f "deb/DEBIAN/control" ]; then
    echo "ERROR: deb/DEBIAN/control not found!"
    exit 1
fi

echo "Building DEB package..."

# Собираем DEB пакет
dpkg-deb --build deb backup-server-agent_1.0.0_amd64.deb

# Проверяем содержимое пакета
echo "Checking package contents..."
echo "All files in package:"
dpkg-deb --contents backup-server-agent_1.0.0_amd64.deb

# Проверяем, что бинарник есть в пакете
if dpkg-deb --contents backup-server-agent_1.0.0_amd64.deb | grep -qE "(usr/bin/backup-server-agent|\./usr/bin/backup-server-agent)"; then
    echo "✓ backup-server-agent binary found in package"
else
    echo "ERROR: backup-server-agent binary not found in package!"
    exit 1
fi

# Проверяем, что конфиг есть в пакете
if dpkg-deb --contents backup-server-agent_1.0.0_amd64.deb | grep -qE "(etc/backupserveragent/config.json|\./etc/backupserveragent/config.json)"; then
    echo "✓ config.json found in package"
else
    echo "ERROR: config.json not found in package!"
    exit 1
fi

# Проверяем, что скрипты есть в пакете
echo "Checking for DEBIAN scripts in package..."

WORK_DIR=$(pwd)

# Проверяем, что пакет создан
if [ ! -f "backup-server-agent_1.0.0_amd64.deb" ]; then
    echo "ERROR: Package file not found!"
    exit 1
fi

# Проверяем размер пакета
PACKAGE_SIZE=$(stat -c%s "backup-server-agent_1.0.0_amd64.deb" 2>/dev/null || stat -f%z "backup-server-agent_1.0.0_amd64.deb" 2>/dev/null || echo "0")
if [ "$PACKAGE_SIZE" -lt 1000 ]; then
    echo "ERROR: Package file is too small ($PACKAGE_SIZE bytes), may be corrupted!"
    exit 1
fi
echo "Package size: $PACKAGE_SIZE bytes"

# Используем dpkg-deb для проверки содержимого control
echo "Checking package control information..."
if dpkg-deb --info backup-server-agent_1.0.0_amd64.deb >/dev/null 2>&1; then
    echo "✓ Package format is valid"
    dpkg-deb --info backup-server-agent_1.0.0_amd64.deb | head -10
else
    echo "ERROR: Package format is invalid!"
    exit 1
fi

# Проверяем наличие скриптов в исходной директории перед сборкой
echo "Verifying source files were included..."
if [ ! -f "deb/DEBIAN/postinst" ]; then
    echo "ERROR: postinst not found in deb/DEBIAN/ before packaging!"
    exit 1
fi

if [ ! -f "deb/DEBIAN/control" ]; then
    echo "ERROR: control not found in deb/DEBIAN/ before packaging!"
    exit 1
fi

# Пытаемся извлечь control файлы из пакета для проверки
# Используем dpkg-deb --control если доступен, иначе проверяем исходные файлы
if command -v dpkg-deb >/dev/null 2>&1; then
    # Пытаемся извлечь control файлы
    TEMP_DIR=$(mktemp -d 2>/dev/null || echo "/tmp/deb_check_$$")
    mkdir -p "$TEMP_DIR"
    
    # Пробуем извлечь control.tar.gz используя dpkg-deb или ar
    if command -v ar >/dev/null 2>&1; then
        cd "$TEMP_DIR"
        if ar -x "$WORK_DIR/backup-server-agent_1.0.0_amd64.deb" control.tar.gz 2>/dev/null; then
            if [ -f "control.tar.gz" ]; then
                tar -xzf control.tar.gz 2>/dev/null && {
                    echo "Files extracted from control.tar.gz:"
                    ls -la
                    if [ -f "postinst" ]; then
                        echo "✓ postinst found in package"
                    else
                        echo "WARNING: postinst not found in extracted control.tar.gz"
                    fi
                } || echo "WARNING: Could not extract control.tar.gz"
            fi
        else
            echo "WARNING: Could not extract control.tar.gz using ar"
        fi
        cd "$WORK_DIR"
    fi
    
    # Альтернативный способ - проверяем через dpkg-deb --control
    if dpkg-deb --control backup-server-agent_1.0.0_amd64.deb "$TEMP_DIR" 2>/dev/null; then
        if [ -f "$TEMP_DIR/postinst" ]; then
            echo "✓ postinst verified in package via dpkg-deb --control"
        fi
    fi
    
    rm -rf "$TEMP_DIR" 2>/dev/null || true
fi

# Финальная проверка - убеждаемся, что файлы были в исходной директории
echo "Source files verification:"
echo "  - deb/DEBIAN/postinst: $([ -f "deb/DEBIAN/postinst" ] && echo "✓ exists" || echo "✗ missing")"
echo "  - deb/DEBIAN/postrm: $([ -f "deb/DEBIAN/postrm" ] && echo "✓ exists" || echo "✗ missing")"
echo "  - deb/DEBIAN/control: $([ -f "deb/DEBIAN/control" ] && echo "✓ exists" || echo "✗ missing")"

# Если postinst отсутствует в исходниках, это критическая ошибка
if [ ! -f "deb/DEBIAN/postinst" ]; then
    echo "ERROR: postinst must exist in deb/DEBIAN/ before packaging!"
    exit 1
fi

echo "DEB package created: backup-server-agent_1.0.0_amd64.deb"

