import { SerialPort } from 'serialport';

/**
 * Manages raw REPL communication with a MicroPython Pico over USB serial.
 *
 * Port of the Python PicoLink class from tools/tui.py and tools/preflight.py.
 * Sends code via raw REPL (Ctrl-A mode), reads OK<stdout>\x04<stderr>\x04> responses.
 */
export class PicoLink {
  private port: SerialPort | null = null;
  private portPath_: string | null;

  constructor(portPath?: string) {
    this.portPath_ = portPath ?? null;
  }

  get portPath(): string | null {
    return this.portPath_;
  }

  /** Auto-detect Pico port: /dev/cu.usbmodem* on macOS, /dev/ttyACM* on Linux */
  async findPort(): Promise<string | null> {
    const ports = await SerialPort.list();
    const pico = ports.find(
      (p) => p.path.includes('usbmodem') || p.path.includes('ttyACM')
    );
    return pico?.path ?? null;
  }

  /** Open serial and enter raw REPL */
  async connect(): Promise<void> {
    const path = this.portPath_ ?? (await this.findPort());
    if (!path) throw new Error('No Pico found');

    this.portPath_ = path;

    this.port = new SerialPort({ path, baudRate: 115200 });
    await new Promise<void>((resolve, reject) => {
      this.port!.on('open', resolve);
      this.port!.on('error', reject);
    });

    // Wait for port to settle
    await this.sleep(100);

    // Interrupt any running program
    this.port.write(Buffer.from('\r\x03\x03'));
    await this.sleep(500);
    this.port.flush();

    // Enter raw REPL (Ctrl-A)
    this.port.write(Buffer.from('\x01'));
    await this.sleep(500);
    await this.drain();
  }

  /** Exit raw REPL and close serial */
  async close(): Promise<void> {
    if (this.port?.isOpen) {
      try {
        this.port.write(Buffer.from('\x02'));
        await this.sleep(100);
      } catch {
        // Ignore errors during cleanup
      }
      await new Promise<void>((resolve) => {
        this.port!.close(() => resolve());
      });
    }
    this.port = null;
  }

  get connected(): boolean {
    return this.port?.isOpen ?? false;
  }

  /**
   * Execute code via raw REPL. Returns { stdout, stderr }.
   * Sends code in 256-byte chunks for flow control,
   * reads OK<stdout>\x04<stderr>\x04> response.
   */
  async execRaw(
    code: string,
    timeout: number = 5000
  ): Promise<{ stdout: string; stderr: string }> {
    if (!this.port?.isOpen) throw new Error('Not connected');

    const data = Buffer.from(code, 'utf-8');
    // Send in 256-byte chunks for flow control
    for (let i = 0; i < data.length; i += 256) {
      this.port.write(data.subarray(i, i + 256));
      await this.sleep(10);
    }
    // Ctrl-D to execute
    this.port.write(Buffer.from('\x04'));

    // Collect response
    return new Promise((resolve, reject) => {
      let buf = Buffer.alloc(0);
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error(`Timeout after ${timeout}ms`));
      }, timeout);

      const onData = (chunk: Buffer) => {
        buf = Buffer.concat([buf, chunk]);
        // Complete: has OK, 2x \x04, ends with >
        if (
          buf.includes(Buffer.from('OK')) &&
          countByte(buf, 0x04) >= 2 &&
          buf[buf.length - 1] === 0x3e // '>'
        ) {
          cleanup();
          const text = buf.toString('utf-8');
          const afterOk = text.split('OK').slice(1).join('OK');
          const parts = afterOk.split('\x04');
          resolve({
            stdout: (parts[0] ?? '').trim(),
            stderr: (parts[1] ?? '').trim(),
          });
        }
      };

      const cleanup = () => {
        clearTimeout(timer);
        this.port?.off('data', onData);
      };

      this.port!.on('data', onData);
    });
  }

  /** Read and discard everything currently in the serial buffer */
  private async drain(): Promise<void> {
    await this.sleep(50);
    if (this.port?.isOpen) {
      this.port.read(); // discard buffered data
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((r) => setTimeout(r, ms));
  }
}

function countByte(buf: Buffer, byte: number): number {
  let count = 0;
  for (let i = 0; i < buf.length; i++) {
    if (buf[i] === byte) count++;
  }
  return count;
}
