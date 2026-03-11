import { useState, useEffect, useRef, useCallback } from 'react';
import { PicoLink } from '../serial/pico-link.js';

interface UsePicoResult {
  connected: boolean;
  portPath: string | null;
  link: PicoLink;
  error: string | null;
  reconnect: () => Promise<void>;
  execRaw: (code: string, timeout?: number) => Promise<{ stdout: string; stderr: string }>;
}

/**
 * React hook that manages PicoLink lifecycle.
 * Connects on mount, cleans up on unmount.
 */
export function usePico(port?: string): UsePicoResult {
  const linkRef = useRef(new PicoLink(port));
  const [connected, setConnected] = useState(false);
  const [portPath, setPortPath] = useState<string | null>(port ?? null);
  const [error, setError] = useState<string | null>(null);

  const doConnect = useCallback(async () => {
    const link = linkRef.current;
    try {
      setError(null);
      await link.connect();
      setConnected(true);
      setPortPath(link.portPath);
    } catch (e) {
      setConnected(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const execRaw = useCallback(
    async (code: string, timeout?: number) => {
      const link = linkRef.current;
      if (!link.connected) throw new Error('Not connected');
      return link.execRaw(code, timeout);
    },
    []
  );

  // Connect on mount
  useEffect(() => {
    doConnect();
    return () => {
      linkRef.current.close().catch(() => {});
    };
  }, [doConnect]);

  return {
    connected,
    portPath,
    link: linkRef.current,
    error,
    reconnect: doConnect,
    execRaw,
  };
}
