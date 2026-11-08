# CustomMail — self-hosted webmail application

This build upgrades the supplied starter into a functioning webmail application with account registration, mailbox login, internal messaging, folders, search, starred messages, drafts, attachments, an admin overview, audit events, and optional outbound SMTP submission.

## Important email boundary

This application currently implements **internal CustomMail-to-CustomMail messaging** using PostgreSQL. It is not, by itself, a public Internet mail server. External inbound mail, third-party OTP reception, and reliable outbound Gmail delivery require a reachable SMTP MX host (normally TCP/25), outbound SMTP access, stable DNS, SPF/DKIM/DMARC, and reverse DNS. InstaBox's HTTPS-published port 8000 is not an MX/SMTP service. Do not publish addresses for external OTP use until the full SMTP path is independently verified.

SMTP submission is optional and disabled unless `SMTP_HOST` is configured. An external recipient is reported as `external_blocked_no_smtp` if no SMTP server is configured. An SMTP submission result means accepted by the configured SMTP server, not guaranteed inbox delivery.

## Deploy on the existing InstaBox machine

1. Back up the existing source, `.env`, and PostgreSQL database before replacing anything.
2. Copy this project into `/data/workspace/custommail` only after verifying backups.
3. Create `/data/custommail/postgres` and `/data/custommail/attachments`.
4. Create `.env` from `.env.example`; use unique strong secrets. Keep `.env` mode `600`.
5. Preserve your existing published port 8000 and existing InstaBox public URL.
6. Run `docker compose config --quiet`, then `docker compose up -d --build`.
7. Verify `docker compose ps`, `docker compose logs --tail=100 app`, and `curl http://127.0.0.1:8000/health`.

Do not delete Docker volumes or `/data` directories. Do not run `docker compose down -v`.

## Default administrator

The bootstrap admin is created as `ADMIN_USERNAME@MAIL_DOMAIN` using `ADMIN_PASSWORD` from `.env` on first startup. Sign in through the normal login form. Keep the administrator password secret. Changing the environment password after bootstrap does not automatically reset the stored user password.

## API overview

- `POST /api/register`
- `POST /api/login`, `POST /api/logout`, `GET /api/me`
- `GET /api/mail/messages?folder=inbox&q=...`
- `GET /api/mail/messages/{id}`
- `POST /api/mail/messages`
- `PATCH /api/mail/messages/{id}/star`
- `DELETE /api/mail/messages/{id}`
- `POST /api/mail/attachments/{message_id}`
- `GET /api/mail/attachments/{attachment_id}`
- Admin: `/api/admin/overview`, `/api/admin/users`, `/api/admin/audit`

## Tests

```bash
python -m pytest -q
```

## Backup

Back up PostgreSQL with `pg_dump` and `/data/custommail` (excluding live database files if using a consistent database dump). Keep backups outside the active data directory and protect them with restrictive permissions.

## Production hardening still required

Before opening public registration broadly, add email verification, password-reset delivery, CSRF tokens for cookie-authenticated state changes, persistent rate limiting (current login throttling is process-local), malware scanning, a mature migration framework, quota enforcement, message retention controls, and security review. The included API is a working development baseline, not a certified production mail provider.
