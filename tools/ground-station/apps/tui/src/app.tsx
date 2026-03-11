import React from 'react';
import { Preflight } from './screens/preflight.js';
import { Postflight } from './screens/postflight.js';

interface AppProps {
  mode: 'preflight' | 'postflight';
  port?: string;
  binFile?: string;
  simFile?: string;
}

export function App({ mode, port, binFile, simFile }: AppProps) {
  if (mode === 'postflight' || binFile) {
    return <Postflight binFile={binFile} simFile={simFile} port={port} />;
  }
  return <Preflight port={port} />;
}
