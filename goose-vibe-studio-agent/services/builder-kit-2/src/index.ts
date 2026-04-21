import express, { Request, Response } from 'express';
import path from 'path';
import { buildS3Client, fetchObject, listSessionObjects } from './minio';
import { detectStack, resolveEntryFile } from './detector';

const PORT = parseInt(process.env.PORT ?? '8080', 10);
const BUCKET = process.env.MINIO_BUCKET ?? 'apps';
const PREFIX = process.env.MINIO_PREFIX ?? 'goose-apps';
const PUBLIC_BASE_URL = process.env.PUBLIC_BASE_URL ?? `http://localhost:${PORT}`;

/**
 * In-memory registry: sessionId → { files: Map<relativePath, Buffer>, entryFile: string }
 * For production use, replace with persistent storage or a real volume mount.
 */
const sessions = new Map<
  string,
  { files: Map<string, Buffer>; entryFile: string; stack: string }
>();

const app = express();
app.use(express.json());

app.post('/deploy', async (req: Request, res: Response) => {
  const { session_id } = req.body as { session_id?: string };

  if (!session_id || typeof session_id !== 'string' || !/^[\w-]+$/.test(session_id)) {
    res.status(400).json({ error: 'Invalid session_id' });
    return;
  }

  // If already deployed, just return the URL (idempotent)
  if (sessions.has(session_id)) {
    res.json({ url: `${PUBLIC_BASE_URL}/apps/${session_id}/` });
    return;
  }

  const client = buildS3Client();
  const sessionPrefix = `${PREFIX}/${session_id}/`;

  let objectKeys: string[];
  try {
    objectKeys = await listSessionObjects(client, BUCKET, sessionPrefix);
  } catch (err) {
    console.error('MinIO list failed:', err);
    res.status(502).json({ error: 'Failed to list files from MinIO' });
    return;
  }

  if (objectKeys.length === 0) {
    res.status(404).json({ error: `No files found for session ${session_id}` });
    return;
  }

  const fileMap = new Map<string, Buffer>();
  await Promise.all(
    objectKeys.map(async (key) => {
      const relativePath = key.slice(sessionPrefix.length);
      if (!relativePath) return;
      try {
        const buf = await fetchObject(client, BUCKET, key);
        fileMap.set(relativePath, buf);
      } catch (err) {
        console.warn(`Failed to fetch ${key}:`, err);
      }
    }),
  );

  const filenames = Array.from(fileMap.keys());
  const stack = detectStack(filenames);
  const entryFile = resolveEntryFile(filenames);

  sessions.set(session_id, { files: fileMap, entryFile, stack });

  console.log(
    `Deployed session=${session_id} stack=${stack} files=${filenames.length} entry=${entryFile}`,
  );

  res.json({ url: `${PUBLIC_BASE_URL}/apps/${session_id}/` });
});

app.get('/apps/:session_id/*', (req: Request, res: Response) => {
  const { session_id } = req.params;
  const session = sessions.get(session_id);

  if (!session) {
    res.status(404).send('App not deployed. Trigger preview first.');
    return;
  }

  const requestedPath = (req.params as Record<string, string>)['0'] || session.entryFile;
  const buf = session.files.get(requestedPath) ?? session.files.get(session.entryFile);

  if (!buf) {
    res.status(404).send('File not found');
    return;
  }

  const ext = path.extname(requestedPath).toLowerCase();
  const mimeTypes: Record<string, string> = {
    '.html': 'text/html',
    '.js': 'application/javascript',
    '.mjs': 'application/javascript',
    '.css': 'text/css',
    '.json': 'application/json',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
  };

  res.setHeader('Content-Type', mimeTypes[ext] ?? 'application/octet-stream');
  res.send(buf);
});

// Health check
app.get('/healthz', (_req, res) => res.json({ status: 'ok' }));

app.listen(PORT, () => {
  console.log(`builder-kit-2 listening on :${PORT}`);
});
