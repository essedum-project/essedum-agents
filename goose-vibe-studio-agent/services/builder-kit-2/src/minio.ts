import { S3Client, ListObjectsV2Command, GetObjectCommand } from '@aws-sdk/client-s3';
import { Readable } from 'stream';

const required = (name: string): string => {
  const val = process.env[name];
  if (!val) throw new Error(`Required env var ${name} is not set`);
  return val;
};

export function buildS3Client(): S3Client {
  return new S3Client({
    endpoint: required('MINIO_ENDPOINT'),
    region: process.env.MINIO_REGION ?? 'us-east-1',
    credentials: {
      accessKeyId: required('MINIO_ACCESS_KEY'),
      secretAccessKey: required('MINIO_SECRET_KEY'),
    },
    forcePathStyle: true,
    tls: process.env.MINIO_TLS_SKIP_VERIFY === 'true' ? false : undefined,
  });
}

export async function listSessionObjects(
  client: S3Client,
  bucket: string,
  prefix: string,
): Promise<string[]> {
  const keys: string[] = [];
  let continuationToken: string | undefined;

  do {
    const resp = await client.send(
      new ListObjectsV2Command({
        Bucket: bucket,
        Prefix: prefix,
        ContinuationToken: continuationToken,
      }),
    );
    for (const obj of resp.Contents ?? []) {
      if (obj.Key) keys.push(obj.Key);
    }
    continuationToken = resp.NextContinuationToken;
  } while (continuationToken);

  return keys;
}

export async function fetchObject(
  client: S3Client,
  bucket: string,
  key: string,
): Promise<Buffer> {
  const resp = await client.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
  const stream = resp.Body as Readable;
  return new Promise<Buffer>((resolve, reject) => {
    const chunks: Buffer[] = [];
    stream.on('data', (chunk: Buffer) => chunks.push(chunk));
    stream.on('end', () => resolve(Buffer.concat(chunks)));
    stream.on('error', reject);
  });
}
