# External mail networking acceptance checklist

CustomMail's internal messaging works independently of public SMTP. External email is not enabled merely by publishing HTTPS port 8000.

Before configuring MX, verify all of the following from outside the InstaBox machine:

- A stable public endpoint accepts raw SMTP on TCP/25.
- The endpoint supports SMTP protocol semantics, including STARTTLS where configured; an HTTPS/SNI TCP proxy may not be transparent.
- Outbound TCP/25 is permitted, or a permitted direct SMTP route exists without using a relay provider.
- SMTP submission on TCP/587 or implicit TLS on TCP/465 behaves correctly.
- IMAPS on TCP/993 works if IMAP clients are required.
- DNS A/AAAA and MX records point to the verified SMTP endpoint.
- PTR/reverse DNS is available and consistent with the SMTP hostname.
- SPF, DKIM, and DMARC records are published and validated.
- TLS certificates cover the actual mail hostname.
- Machine suspension/runtime behavior does not make MX delivery unreliable.

Do not publish MX records or advertise the domain as receiving external mail until these tests pass. If InstaBox's public TCP service terminates TLS or only supports an SNI-wrapped stream, test SMTP/STARTTLS explicitly; do not assume compatibility.

Current application behavior:

- CustomMail-to-CustomMail messages are stored in PostgreSQL and appear in recipient inboxes.
- External recipients are blocked with status `external_blocked_no_smtp` when `SMTP_HOST` is empty.
- When configured, outbound messages are submitted to the configured SMTP server; `submitted_to_smtp` means server submission only, not guaranteed inbox delivery.
- There is no inbound SMTP listener or IMAP server in this build. Therefore third-party OTP email reception and external IMAP access are not implemented yet.
