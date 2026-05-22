import { S3Client, PutObjectCommand, DeleteObjectCommand } from '@aws-sdk/client-s3'

const {
  R2_ACCOUNT_ID,
  R2_ACCESS_KEY_ID,
  R2_SECRET_ACCESS_KEY,
  R2_BUCKET,
  R2_PUBLIC_URL,
} = process.env

function assertR2(): asserts R2_ACCOUNT_ID is string {
  if (!R2_ACCOUNT_ID || !R2_ACCESS_KEY_ID || !R2_SECRET_ACCESS_KEY || !R2_BUCKET || !R2_PUBLIC_URL) {
    throw new Error('Missing R2_* env vars')
  }
}

function client() {
  assertR2()
  return new S3Client({
    region: 'auto',
    endpoint: `https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
    credentials: {
      accessKeyId: R2_ACCESS_KEY_ID!,
      secretAccessKey: R2_SECRET_ACCESS_KEY!,
    },
  })
}

export async function uploadStoryImage(
  key: string,
  body: Buffer,
  contentType: 'image/jpeg' | 'image/png',
): Promise<string> {
  assertR2()
  const s3 = client()
  await s3.send(
    new PutObjectCommand({
      Bucket: R2_BUCKET!,
      Key: key,
      Body: body,
      ContentType: contentType,
      CacheControl: 'public, max-age=300',
    }),
  )
  const base = R2_PUBLIC_URL!.replace(/\/+$/, '')
  return `${base}/${key}`
}

export async function deleteObject(key: string): Promise<void> {
  assertR2()
  const s3 = client()
  await s3.send(new DeleteObjectCommand({ Bucket: R2_BUCKET!, Key: key }))
}
