import { defineConfig, Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { readdirSync, statSync, readFileSync, existsSync } from 'fs';
import { join, resolve } from 'path';

/** Known macOS system volumes to skip */
const SYSTEM_VOLUMES = new Set([
  'Macintosh HD', 'Macintosh HD - Data', 'Recovery', 'Preboot', 'VM',
  'Update', 'com.apple.TimeMachine.localsnapshots',
]);

interface FileEntry {
  name: string;
  size: number;
  mtime: string;
  source: string; // 'local' | volume name
  folder: boolean; // true if this flight lives in a per-flight folder
}

/** Scan a directory for flight folders (subdirs containing flight.bin). */
function scanFlightFolders(dir: string, source: string): FileEntry[] {
  const results: FileEntry[] = [];
  try {
    for (const entry of readdirSync(dir)) {
      if (entry.startsWith('.')) continue;
      const entryPath = join(dir, entry);
      try {
        if (!statSync(entryPath).isDirectory()) continue;
        const binPath = join(entryPath, 'flight.bin');
        if (existsSync(binPath)) {
          const st = statSync(binPath);
          if (st.size > 50) {
            results.push({
              name: entry,  // folder name as identifier
              size: st.size,
              mtime: st.mtime.toISOString(),
              source,
              folder: true,
            });
          }
        }
      } catch {}
    }
  } catch {}
  return results;
}

/**
 * Scan mounted volumes for .bin flight logs.
 * Supports both per-flight folders (flight_001/flight.bin) and legacy flat .bin files.
 */
function findVolumeFiles(ext: string): FileEntry[] {
  const results: FileEntry[] = [];
  if (!existsSync('/Volumes')) return results;

  try {
    const volumes = readdirSync('/Volumes').filter(
      (v) => !SYSTEM_VOLUMES.has(v) && !v.startsWith('.'),
    );
    for (const vol of volumes) {
      const volPath = join('/Volumes', vol);
      try {
        if (!statSync(volPath).isDirectory()) continue;
        const scanDirs = [volPath];
        const sdPath = join(volPath, 'sd');
        if (existsSync(sdPath)) scanDirs.push(sdPath);

        for (const dir of scanDirs) {
          // Scan for per-flight folders
          results.push(...scanFlightFolders(dir, vol));

          // Legacy flat .bin files
          const files = readdirSync(dir).filter((f) => f.endsWith(ext));
          for (const f of files) {
            try {
              const st = statSync(join(dir, f));
              if (st.size > 50) {
                results.push({
                  name: f,
                  size: st.size,
                  mtime: st.mtime.toISOString(),
                  source: vol,
                  folder: false,
                });
              }
            } catch {}
          }
        }
      } catch {}
    }
  } catch {}
  return results;
}

/** Resolve a file path from a source + name. Handles both folder-based and flat files. */
function resolveFilePath(
  name: string,
  source: string,
  ext: string,
  localDirs: string[],
  isFolder: boolean = false,
): string | null {
  // For folder-based flights, the name is the folder and the file inside is flight.bin
  const fileName = isFolder ? join(name, 'flight.bin') : name;

  // Local directories first
  if (source === 'local') {
    for (const dir of localDirs) {
      const p = join(dir, fileName);
      if (existsSync(p)) return p;
    }
    return null;
  }
  // Volume source
  const volPath = join('/Volumes', source);
  const candidates = [join(volPath, fileName), join(volPath, 'sd', fileName)];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return null;
}

/** Resolve the preflight.txt path for a folder-based flight. */
function resolvePreflightPath(
  name: string,
  source: string,
  localDirs: string[],
): string | null {
  const fileName = join(name, 'preflight.txt');
  if (source === 'local') {
    for (const dir of localDirs) {
      const p = join(dir, fileName);
      if (existsSync(p)) return p;
    }
    return null;
  }
  const volPath = join('/Volumes', source);
  const candidates = [join(volPath, fileName), join(volPath, 'sd', fileName)];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return null;
}

/**
 * Vite plugin that exposes flight/sim file discovery and serving APIs
 * so the web dashboard can auto-load files like the TUI.
 */
function flightFilesPlugin(): Plugin {
  // Walk up from apps/web/ to find the repo root
  const repoRoot = resolve(__dirname, '../../../..');
  const flightsDir = join(repoRoot, 'flights');
  const simsDir = join(repoRoot, 'sims');

  return {
    name: 'mpr-flight-files',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/api/')) return next();

        // CORS for dev
        res.setHeader('Content-Type', 'application/json');

        // GET /api/flights — list .bin files from flights/ + volumes
        if (req.url === '/api/flights' && req.method === 'GET') {
          const files: FileEntry[] = [];

          // Local flights/ directory — scan for flight folders
          if (existsSync(flightsDir)) {
            files.push(...scanFlightFolders(flightsDir, 'local'));

            // Also scan for legacy flat .bin files
            try {
              for (const f of readdirSync(flightsDir).filter((f) => f.endsWith('.bin'))) {
                try {
                  const st = statSync(join(flightsDir, f));
                  if (st.size > 50) {
                    files.push({
                      name: f,
                      size: st.size,
                      mtime: st.mtime.toISOString(),
                      source: 'local',
                      folder: false,
                    });
                  }
                } catch {}
              }
            } catch {}
          }

          // Volumes
          files.push(...findVolumeFiles('.bin'));

          // Sort newest first
          files.sort((a, b) => new Date(b.mtime).getTime() - new Date(a.mtime).getTime());
          res.end(JSON.stringify(files));
          return;
        }

        // GET /api/sims — list .csv files from sims/
        if (req.url === '/api/sims' && req.method === 'GET') {
          const files: FileEntry[] = [];
          if (existsSync(simsDir)) {
            try {
              for (const f of readdirSync(simsDir).filter((f) => f.endsWith('.csv'))) {
                try {
                  const st = statSync(join(simsDir, f));
                  files.push({
                    name: f,
                    size: st.size,
                    mtime: st.mtime.toISOString(),
                    source: 'local',
                    folder: false,
                  });
                } catch {}
              }
            } catch {}
          }
          files.sort((a, b) => new Date(b.mtime).getTime() - new Date(a.mtime).getTime());
          res.end(JSON.stringify(files));
          return;
        }

        // GET /api/flights/:name/preflight?source=...&folder=true — serve preflight.txt
        const preflightMatch = req.url.match(/^\/api\/flights\/([^/]+)\/preflight(\?.*)?$/);
        if (preflightMatch && req.method === 'GET') {
          const name = decodeURIComponent(preflightMatch[1]);
          const params = new URL(req.url, 'http://localhost').searchParams;
          const source = params.get('source') || 'local';
          const filePath = resolvePreflightPath(name, source, [flightsDir]);

          if (!filePath) {
            res.statusCode = 404;
            res.end(JSON.stringify({ error: 'No preflight.txt found' }));
            return;
          }

          try {
            const data = readFileSync(filePath, 'utf-8');
            res.setHeader('Content-Type', 'text/plain');
            res.end(data);
          } catch (e) {
            res.statusCode = 500;
            res.end(JSON.stringify({ error: String(e) }));
          }
          return;
        }

        // GET /api/flights/:name?source=...&folder=... — serve a .bin file
        const flightMatch = req.url.match(/^\/api\/flights\/([^?/]+)(\?.*)?$/);
        if (flightMatch && req.method === 'GET') {
          const name = decodeURIComponent(flightMatch[1]);
          const params = new URL(req.url, 'http://localhost').searchParams;
          const source = params.get('source') || 'local';
          const isFolder = params.get('folder') === 'true';
          const filePath = resolveFilePath(name, source, '.bin', [flightsDir], isFolder);

          if (!filePath) {
            res.statusCode = 404;
            res.end(JSON.stringify({ error: 'File not found' }));
            return;
          }

          try {
            const data = readFileSync(filePath);
            res.setHeader('Content-Type', 'application/octet-stream');
            res.end(data);
          } catch (e) {
            res.statusCode = 500;
            res.end(JSON.stringify({ error: String(e) }));
          }
          return;
        }

        // GET /api/sims/:name — serve a .csv file
        const simMatch = req.url.match(/^\/api\/sims\/([^?]+)(\?.*)?$/);
        if (simMatch && req.method === 'GET') {
          const name = decodeURIComponent(simMatch[1]);
          const filePath = join(simsDir, name);

          if (!existsSync(filePath)) {
            res.statusCode = 404;
            res.end(JSON.stringify({ error: 'File not found' }));
            return;
          }

          try {
            const data = readFileSync(filePath, 'utf-8');
            res.setHeader('Content-Type', 'text/csv');
            res.end(data);
          } catch (e) {
            res.statusCode = 500;
            res.end(JSON.stringify({ error: String(e) }));
          }
          return;
        }

        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), flightFilesPlugin()],
  resolve: {
    preserveSymlinks: true,
  },
});
