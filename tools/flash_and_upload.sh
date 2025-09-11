#!/bin/bash

# === Step 1: Detect ESP32 serial port ===
echo "🔍 Scanning for ESP32 serial ports..."
PORTS=(/dev/tty.usb*)

if [ ${#PORTS[@]} -eq 0 ]; then
    echo "❌ No ESP32 devices found (no /dev/tty.usb* ports)."
    exit 1
elif [ ${#PORTS[@]} -eq 1 ]; then
    PORT="${PORTS[0]}"
    echo "✅ Found one port: $PORT"
else
    echo "⚠️ Multiple ports found:"
    select PORT in "${PORTS[@]}"; do
        if [ -n "$PORT" ]; then
            echo "✅ Selected port: $PORT"
            break
        else
            echo "Invalid selection."
        fi
    done
fi

# === Step 2: Flash firmware ===
FIRMWARE="./firmware/ESP32_GENERIC_C3-20250809-v1.26.0.bin"
echo "🧽 Erasing flash on $PORT..."
esptool.py --chip esp32c3 --port "$PORT" erase_flash

echo "💾 Writing firmware to $PORT..."
esptool.py --chip esp32c3 --port "$PORT" write_flash -z 0x0 "$FIRMWARE"

# === Step 3: Wait for reboot ===
echo "⏳ Waiting 5 seconds for device reboot..."
sleep 5

# === Step 4: Remove all files from filesystem ===
echo "🗑️ Removing all files on device filesystem..."
mpremote connect auto fs rm -rv :/

# === Step 5: Copy new source code ===
echo "📁 Uploading new source files from ./src/..."
mpremote connect auto cp -r ./src/* :/

echo "✅ Flash and upload complete."

mpremote connect auto reset repl