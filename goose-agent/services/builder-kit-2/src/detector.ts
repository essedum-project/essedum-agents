/**
 * Detect what kind of web app a file set represents.
 * Returns: 'static' | 'spa' | 'node'
 */
export type StackType = 'static' | 'spa' | 'node';

export function detectStack(filenames: string[]): StackType {
  const names = filenames.map((f) => f.toLowerCase());

  if (names.includes('package.json') && names.some((n) => n.endsWith('server.js') || n.endsWith('server.ts'))) {
    return 'node';
  }

  if (names.some((n) => n.endsWith('.jsx') || n.endsWith('.tsx') || n === 'vite.config.js' || n === 'vite.config.ts')) {
    return 'spa';
  }

  return 'static';
}

export function resolveEntryFile(filenames: string[]): string {
  if (filenames.includes('index.html')) return 'index.html';
  const html = filenames.find((f) => f.endsWith('.html'));
  if (html) return html;
  return filenames[0] ?? 'index.html';
}
