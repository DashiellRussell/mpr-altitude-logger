import { SerialPort } from 'serialport';

// SOFT_RESET_CODE in commands.ts is kept for reference but not used here.
// We use Ctrl-B + Ctrl-D instead of machine.soft_reset() to keep USB alive.

export type PicoLinkMode = 'repl' | 'passthrough';
export type LineListener = (line: string) => void;

/**
 * Manages raw REPL communication with a MicroPython Pico over USB serial.
 *
 * Port of the Python PicoLink class from tools/tui.py and tools/preflight.py.
 * Sends code via raw REPL (Ctrl-A mode), reads OK<stdout>\x04<stderr>\x04> responses.
 *
 * After softReset(), switches to passthrough mode where incoming bytes are
 * accumulated into lines and emitted to registered listeners.
 */
export class PicoLink {
  private port: SerialPort | null = null;
  private portPath_: string | null;
  private mode_: PicoLinkMode = 'repl';
  private lineBuffer = '';
  private lineListeners: LineListener[] = [];
  private noiseWindowEnd = 0;
  /** Debug counters — visible from outside for diagnostics */
  public debugBytesReceived = 0;
  public debugDataEvents = 0;
  public debugPortError: string | null = null;
  public debugLineBufferLen = 0;
  public debugLineBufferHead = '';

  constructor(portPath?: string) {
    this.portPath_ = portPath ?? null;
  }

  get portPath(): string | null {
    return this.portPath_;
  }

  get mode(): PicoLinkMode {
    return this.mode_;
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

    this.mode_ = 'repl';
  }

  /** Exit raw REPL and close serial */
  async close(): Promise<void> {
    if (this.port?.isOpen) {
      try {
        if (this.mode_ === 'repl') {
          this.port.write(Buffer.from('\x02'));
        }
        await this.sleep(100);
      } catch {
        // Ignore errors during cleanup
      }
      this.port.removeAllListeners('data');
      await new Promise<void>((resolve) => {
        this.port!.close(() => resolve());
      });
    }
    this.port = null;
    this.lineListeners = [];
    this.lineBuffer = '';
    this.mode_ = 'repl';
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
    if (this.mode_ !== 'repl') throw new Error('Not in REPL mode');

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

  /**
   * Trigger a soft reboot into main.py and switch to passthrough line reading.
   *
   * Uses Ctrl-B (exit raw REPL → normal REPL) then Ctrl-D (soft reboot at
   * the >>> prompt). This keeps USB CDC alive — no port drop, no reconnect.
   * machine.soft_reset() is NOT used because it re-enumerates USB on RP2040,
   * killing the serial connection.
   *
   * The passthrough data handler is installed BEFORE sending the reboot
   * sequence so we never miss early boot output from main.py.
   */
  async softReset(): Promise<void> {
    if (!this.port?.isOpen) throw new Error('Not connected');

    // Reset debug counters
    this.debugBytesReceived = 0;
    this.debugDataEvents = 0;
    this.debugPortError = null;

    // Switch to passthrough mode BEFORE sending reboot so we catch all output
    this.mode_ = 'passthrough';
    this.lineBuffer = '';
    this.noiseWindowEnd = Date.now() + 2000;

    // Replace all data listeners with passthrough handler
    this.port.removeAllListeners('data');
    this.port.on('data', (chunk: Buffer) => {
      this.debugDataEvents++;
      this.debugBytesReceived += chunk.length;
      this.handlePassthroughData(chunk);
    });

    // Track port errors/close
    this.port.once('error', (err: Error) => {
      this.debugPortError = `error: ${err.message}`;
    });
    this.port.once('close', () => {
      this.debugPortError = 'port closed unexpectedly';
    });

    // Ensure the stream is flowing
    this.port.resume();

    // Step 1: Ctrl-B — exit raw REPL, enter normal REPL (>>> prompt)
    this.port.write(Buffer.from('\x02'));
    await this.sleep(500);

    // Step 2: Ctrl-C twice — clear any stale input
    this.port.write(Buffer.from('\x03\x03'));
    await this.sleep(300);

    // Step 3: Type the reboot command at the >>> prompt.
    // Ctrl-D is unreliable for triggering soft reboot (requires empty
    // input line, timing-sensitive). Instead, explicitly run
    // machine.soft_reset() as normal REPL input — this always works.
    this.port.write(Buffer.from('import machine; machine.soft_reset()\r\n'));
  }

  /** Register a line listener for passthrough mode */
  onLine(cb: LineListener): void {
    this.lineListeners.push(cb);
  }

  /** Unregister a line listener */
  offLine(cb: LineListener): void {
    this.lineListeners = this.lineListeners.filter((l) => l !== cb);
  }

  /**
   * Handle incoming bytes in passthrough mode.
   * Strips non-printable control chars, accumulates into lines,
   * emits complete lines to registered listeners.
   */
  private handlePassthroughData(chunk: Buffer): void {
    let text = chunk.toString('utf-8');

    // Strip non-printable control chars (except \n, \r, \t)
    // This handles raw REPL framing bytes (\x04 EOT, \x01 SOH, etc.)
    text = text.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '');

    if (!text) return;

    this.lineBuffer += text;
    this.debugLineBufferLen = this.lineBuffer.length;
    this.debugLineBufferHead = this.lineBuffer.slice(0, 120).replace(/[^\x20-\x7e]/g, '?');

    // Emit complete lines
    const lines = this.lineBuffer.split('\n');
    // Keep the last incomplete chunk in the buffer
    this.lineBuffer = lines.pop() ?? '';

    for (const line of lines) {
      const trimmed = line.replace(/\r$/, '');
      if (trimmed.length > 0) {
        for (const cb of this.lineListeners) {
          cb(trimmed);
        }
      }
    }
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
