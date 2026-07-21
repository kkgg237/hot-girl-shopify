// Failure alerts. When a scheduled post/story fails to publish we want to know
// immediately — the June 2026 token expiry failed silently for three weeks.
//
// Email is gated entirely on .env config (SMTP_*). If it isn't configured the
// send is skipped with a logged warning — the in-app red banner still works, so
// nothing here is load-bearing. nodemailer is imported dynamically so the server
// boots even if the package isn't installed yet.
//
// Required .env to enable email:
//   SMTP_HOST=smtp.your-provider.com
//   SMTP_PORT=465
//   SMTP_USER=you@hysteric.shop
//   SMTP_PASS=app-password-or-smtp-password
//   ALERT_EMAIL_TO=kat@hysteric.shop        (optional; defaults to SMTP_USER)
//   ALERT_EMAIL_FROM="Past Studies <you@hysteric.shop>"  (optional; defaults to SMTP_USER)

const {
  SMTP_HOST,
  SMTP_PORT,
  SMTP_USER,
  SMTP_PASS,
  ALERT_EMAIL_TO,
  ALERT_EMAIL_FROM,
} = process.env

export function emailConfigured(): boolean {
  return Boolean(SMTP_HOST && SMTP_USER && SMTP_PASS)
}

export async function sendFailureEmail(subject: string, body: string): Promise<void> {
  if (!emailConfigured()) {
    console.warn(
      `[notify] email not configured — skipping alert "${subject}". ` +
        `Set SMTP_HOST/SMTP_USER/SMTP_PASS in .env to enable.`,
    )
    return
  }
  try {
    const nodemailer = (await import('nodemailer')).default
    const port = Number(SMTP_PORT ?? 465)
    const transport = nodemailer.createTransport({
      host: SMTP_HOST,
      port,
      secure: port === 465, // 465 = implicit TLS; 587 = STARTTLS
      auth: { user: SMTP_USER, pass: SMTP_PASS },
    })
    await transport.sendMail({
      from: ALERT_EMAIL_FROM || SMTP_USER,
      to: ALERT_EMAIL_TO || SMTP_USER,
      subject,
      text: body,
    })
    console.log(`[notify] failure email sent: "${subject}"`)
  } catch (e) {
    // Never let alerting throw into the scheduler — just log it.
    console.error('[notify] failed to send failure email:', e instanceof Error ? e.message : e)
  }
}

// Convenience wrapper used by the scheduler for a failed scheduled post.
export async function notifyScheduledFailure(kind: string, label: string, error: string): Promise<void> {
  const subject = `⚠️ Past Studies: ${kind} failed to post`
  const body =
    `A scheduled ${kind} failed to publish to Instagram.\n\n` +
    `Item: ${label}\n` +
    `Error: ${error}\n\n` +
    `Open the stories tool → Calendar to review and retry.`
  await sendFailureEmail(subject, body)
}
