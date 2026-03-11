import type { PicoLink } from './pico-link.js';

/**
 * SD card file download utilities.
 * Port of the download logic from tools/postflight.py.
 */

export interface BinFileInfo {
  name: string;
  size: number;
}

/**
 * List .bin flight log files on the Pico's SD card.
 * Mounts the SD card, reads the directory, and unmounts.
 */
export async function listBinFiles(link: PicoLink): Promise<BinFileInfo[]> {
  const code = `
import os
from machine import SPI, Pin
import sdcard
spi = SPI(0, baudrate=1000000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs = Pin(17, Pin.OUT, value=1)
sd = sdcard.SDCard(spi, cs)
vfs = os.VfsFat(sd)
os.mount(vfs, '/sd')
for f in os.listdir('/sd'):
    if f.endswith('.bin'):
        stat = os.stat('/sd/' + f)
        print(f + '|' + str(stat[6]))
os.umount('/sd')
`;

  const { stdout, stderr } = await link.execRaw(code, 10000);
  if (stderr) {
    throw new Error(`Error listing files: ${stderr}`);
  }

  const files: BinFileInfo[] = [];
  for (const line of stdout.split('\n')) {
    const trimmed = line.trim();
    if (trimmed.includes('|')) {
      const [name, sizeStr] = trimmed.split('|', 2);
      files.push({ name: name.trim(), size: parseInt(sizeStr.trim(), 10) });
    }
  }
  return files;
}

/**
 * Download a file from the Pico's SD card via base64 chunks.
 *
 * The Pico reads the file in 512-byte chunks, base64-encodes each,
 * and prints them line by line. We collect lines until we see 'EOF'.
 *
 * Uses execRaw with a large timeout since files can be big.
 * The onProgress callback receives cumulative bytes downloaded.
 */
export async function downloadFile(
  link: PicoLink,
  filename: string,
  onProgress?: (bytes: number) => void
): Promise<Uint8Array> {
  // Sanitize filename to prevent injection
  const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, '');

  const code = `
import os, ubinascii
from machine import SPI, Pin
import sdcard
spi = SPI(0, baudrate=1000000, polarity=0, phase=0, sck=Pin(18), mosi=Pin(19), miso=Pin(16))
cs = Pin(17, Pin.OUT, value=1)
sd = sdcard.SDCard(spi, cs)
vfs = os.VfsFat(sd)
os.mount(vfs, '/sd')
f = open('/sd/${safeName}', 'rb')
while True:
    d = f.read(512)
    if not d:
        break
    print(ubinascii.b2a_base64(d).decode().strip())
print('EOF')
f.close()
os.umount('/sd')
`;

  // Use a long timeout (2 minutes) for large files
  const { stdout, stderr } = await link.execRaw(code, 120000);
  if (stderr) {
    throw new Error(`Download error: ${stderr}`);
  }

  // Decode base64 lines
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;

  for (const line of stdout.split('\n')) {
    const trimmed = line.trim();
    if (trimmed === 'EOF' || trimmed === '') continue;

    try {
      const decoded = Buffer.from(trimmed, 'base64');
      chunks.push(new Uint8Array(decoded));
      totalBytes += decoded.length;
      onProgress?.(totalBytes);
    } catch {
      // Skip non-base64 lines (REPL noise)
    }
  }

  // Concatenate all chunks
  const result = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }

  return result;
}
