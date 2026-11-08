# CustomMail

**CustomMail** is a self-hosted email application built with FastAPI and
PostgreSQL. It provides a webmail interface for a configured domain,
receives external email through Mailumi webhooks, submits outbound email
through the Mailumi HTTP API, and offers disposable temporary inboxes.

> **Deployment target:** InstaBox.sh\
> **Application domain:** `mailus.aclclouds.online` (replace with your
> own verified domain)\
> **Application stack:** Python 3.11, FastAPI, SQLAlchemy, PostgreSQL,
> Docker Compose\
> **Mail provider integration:** Mailumi inbound webhooks and outbound
> API

This README describes the application, configuration, deployment,
operation, and security practices. Keep production credentials in
InstaBox secrets or a private environment file. **Never commit real
secrets to GitHub.**

------------------------------------------------------------------------

## Contents

-   [Features](#features)
-   [Architecture](#architecture)
-   [Repository layout](#repository-layout)
-   [Requirements](#requirements)
-   [Configuration](#configuration)
-   [Mailumi setup](#mailumi-setup)
-   [DNS configuration](#dns-configuration)
-   [Deploy on InstaBox.sh](#deploy-on-instaboxsh)
-   [Run locally for development](#run-locally-for-development)
-   [Using CustomMail](#using-custommail)
-   [API routes](#api-routes)
-   [Tests](#tests)
-   [Backups and recovery](#backups-and-recovery)
-   [Security notes](#security-notes)
-   [Troubleshooting](#troubleshooting)
-   [Limitations](#limitations)
-   [Contributing](#contributing)
-   [License](#license)

------------------------------------------------------------------------

## Features

### Webmail

-   User authentication and mailbox access.
-   Web interface for viewing and composing messages.
-   Internal delivery between CustomMail mailboxes.
-   Message and attachment storage in the configured storage directory.
-   Audit records for relevant mail actions.

### External inbound email

-   Mailumi receives mail for the configured domain.
-   Mailumi sends `email.received` events to the CustomMail webhook.
-   The webhook validates the provider signature before processing the
    event.
-   Messages addressed to active CustomMail mailboxes are stored in the
    application database.
-   Duplicate webhook event IDs are handled using event deduplication.
-   Supported message content includes text and HTML-derived text.
-   Eligible attachments are downloaded from Mailumi and stored with the
    message.

### External outbound email

-   Outbound messages are submitted using the Mailumi HTTP API.
-   The application uses a separate send API key from the inbound/read
    key.
-   To, CC, and BCC recipients are handled separately.
-   Internal CustomMail recipients can be delivered within the
    application.
-   The application records the provider response and submission status.

**Important:** A successful API submission means Mailumi accepted the
message for processing. It does not prove that the recipient's mail
server delivered it to the inbox.

### Temporary Mail

-   Create disposable inboxes without creating a normal user account.
-   Temporary inboxes expire after 24 hours.
-   Inbox access is protected by a random token; the server stores a
    hash of the token.
-   Temporary inboxes can receive messages through the same Mailumi
    inbound webhook.
-   View and delete received messages and attachments.
-   Temporary inbox creation is rate-limited and active inboxes per IP
    are limited.

Do not use temporary inboxes for sensitive, permanent, financial, or
account-recovery email.

------------------------------------------------------------------------

## Architecture

``` text
                         Internet
                            |
                 DNS: mailus.aclclouds.online
                            |
                     Mailumi service
                       /         \
             Inbound email     Outbound API
                   |                 ^
                   v                 |
           Mailumi webhook       CustomMail API
                   |                 ^
                   v                 |
          InstaBox public URL -------+
                   |
             FastAPI application
              /      |       \
             /       |        \
       Webmail    Mail logic   Temp Mail
             \       |        /
              \      v       /
                 PostgreSQL
                     |
              Persistent storage
```

### Main components

  -----------------------------------------------------------------------
  Component                           Responsibility
  ----------------------------------- -----------------------------------
  FastAPI                             HTTP application, pages,
                                      authentication, and API routes

  SQLAlchemy                          Database models and persistence

  PostgreSQL                          Users, mailboxes, messages,
                                      attachments metadata, audit events,
                                      webhook events, and temporary
                                      mailbox records

  Mailumi                             External inbound mail processing
                                      and outbound email submission

  Docker Compose                      Runs the application and database
                                      containers

  InstaBox.sh                         Hosts the application and provides
                                      runtime secrets/networking

  Static frontend                     HTML, CSS, and JavaScript webmail
                                      and Temp Mail interface
  -----------------------------------------------------------------------

### Email flow

**Inbound:** Sender → Mailumi → signed webhook → CustomMail → mailbox
lookup → PostgreSQL/storage → inbox UI.

**Outbound:** Compose message → CustomMail → internal delivery (if
applicable) and/or Mailumi send API → provider queues the message →
recipient mail system.

------------------------------------------------------------------------

## Repository layout

``` text
custommail/
├── app/
│   ├── config.py          # Environment and application settings
│   ├── db.py              # Database engine and session handling
│   ├── inbound.py         # Raw inbound email handling
│   ├── mail.py            # Mail-related application logic
│   ├── mailumi.py         # Mailumi webhook and API integration
│   ├── main.py            # FastAPI routes and application entry point
│   ├── models.py          # SQLAlchemy database models
│   ├── security.py        # Authentication and security helpers
│   └── temp_mail.py       # Temporary mailbox logic
├── static/
│   ├── index.html         # Main webmail page
│   ├── app.js             # Webmail frontend
│   ├── style.css          # Main styles
│   ├── temp-mail.html     # Temporary inbox page
│   ├── temp-mail.js       # Temporary inbox frontend
│   └── temp-mail.css     # Temporary inbox styles
├── tests/
│   ├── test_app.py
│   ├── test_inbound.py
│   ├── test_outbound.py
│   └── test_temp_mail.py
├── ops/
│   ├── backup.sh
│   ├── verify.sh
│   ├── MAILSERVER.md
│   ├── NETWORK-LIMITATIONS.md
│   └── provision-mailbox-docker-mailserver.sh
├── .env.example           # Safe configuration template
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

------------------------------------------------------------------------

## Requirements

### Production

-   InstaBox.sh account and a running box.
-   Docker and Docker Compose available in the box.
-   A domain or subdomain whose DNS records you can manage.
-   A Mailumi account with the required domain and API features enabled.
-   InstaBox public application URL or configured public ingress.
-   Persistent storage for PostgreSQL and application data.
-   A valid TLS certificate for the public application endpoint.

### Local development

-   Python 3.11+
-   pip
-   Git
-   Docker Engine and Docker Compose (recommended for PostgreSQL)
-   A Mailumi account only if testing real external email

------------------------------------------------------------------------

## Configuration

CustomMail reads settings from environment variables through
`app/config.py`. `.env.example` is a template; copy it to `.env` for
local development and fill in values privately.

``` bash
cp .env.example .env
```

Do not put production `.env` files in Git. The repository `.gitignore`
excludes `.env` and common secret/database files.

### Environment variables

The following are the main settings used by the application and Compose
deployment. Check `app/config.py` and `docker-compose.yml` in the
version you deploy if you change the configuration.

  --------------------------------------------------------------------------------------------------------
  Variable                   Purpose                 Example / guidance
  -------------------------- ----------------------- -----------------------------------------------------
  `APP_SECRET`               Secret used for         Generate a long random value
                             application             
                             security/session        
                             signing                 

  `ADMIN_USERNAME`           Initial/admin login     Choose a non-default username
                             username                

  `ADMIN_PASSWORD`           Initial/admin password  Use a unique strong password
                             setting                 

  `DATABASE_URL`             SQLAlchemy database     `postgresql+psycopg://USER:PASSWORD@db:5432/DBNAME`
                             connection              (match your Compose service and credentials)

  `MAIL_DOMAIN`              Domain used for         `mailus.aclclouds.online`
                             CustomMail addresses    

  `APP_BASE_URL`             Public base URL of the  `https://your-public-app-url`
                             application             

  `STORAGE_DIR`              Directory for           `/data/custommail`
                             message/attachment      
                             files                   

  `COOKIE_SECURE`            Secure-cookie setting   `true` when served over HTTPS; `false` only for local
                                                     HTTP testing

  `INBOUND_EMAIL_SECRET`     Secret for the raw      Generate a separate random value
                             inbound endpoint, if    
                             used                    

  `MAILUMI_API_KEY`          Mailumi read key for    Key with only required read permissions
                             inbound                 
                             email/attachment API    
                             operations              

  `MAILUMI_SEND_API_KEY`     Mailumi outbound        Separate key with `email:send` permission
                             sending key             

  `MAILUMI_WEBHOOK_SECRET`   Secret used to verify   Use the signing secret from the Mailumi webhook
                             Mailumi webhook         configuration
                             signatures              
  --------------------------------------------------------------------------------------------------------

Some settings may have defaults in `app/config.py`. Do not assume a
default is suitable for production.

### Generate strong secrets

On Linux, generate a random secret locally:

``` bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Run this separately for each secret. Do not reuse the same value for
`APP_SECRET`, webhook signing, and other credentials.

### Database URL format

For PostgreSQL, use the driver and service name configured in your
Compose file. A typical format is:

``` text
postgresql+psycopg://USERNAME:PASSWORD@db:5432/DATABASE
```

The hostname `db` works only when the application and PostgreSQL
containers share the Compose network and the database service is named
`db`. Use the actual service name and credentials in your deployment.

For isolated local tests, the project test suite can use SQLite;
production should use the persistent PostgreSQL service configured in
`docker-compose.yml`.

### InstaBox secrets

For production, store credentials in InstaBox's secret manager rather
than committing them or placing them in a public file.

The deployment must make these variables available to the application
container:

-   `APP_SECRET`
-   `ADMIN_USERNAME`
-   `ADMIN_PASSWORD`
-   `DATABASE_URL`
-   `MAIL_DOMAIN`
-   `APP_BASE_URL`
-   `STORAGE_DIR`
-   `COOKIE_SECURE`
-   `MAILUMI_API_KEY`
-   `MAILUMI_SEND_API_KEY`
-   `MAILUMI_WEBHOOK_SECRET`

The exact InstaBox CLI syntax can change. Use the current InstaBox
documentation/CLI help for secret creation, selection, and injection.
After changing secret selections, recreate or restart only the
application container as required by the platform. Do not remove the
database volume.

------------------------------------------------------------------------

## Mailumi setup

CustomMail uses Mailumi for external email. Inbound receiving and
outbound sending are separate configurations.

### 1. Verify your domain in Mailumi

1.  Add the domain you intend to use, for example
    `mailus.aclclouds.online`.
2.  Copy the exact verification and DKIM records shown by Mailumi.
3.  Add those records to your DNS provider.
4.  Wait for DNS propagation.
5.  Return to Mailumi and confirm that the domain is verified.

Do not copy sample DKIM values from this README. Use the exact hostnames
and values generated in your own Mailumi account.

### 2. Configure inbound receiving

1.  Enable inbound receiving for the verified domain in Mailumi.
2.  Configure the MX record specified by Mailumi.
3.  Create an API key with only the permissions required to read inbound
    messages and attachments.
4.  Store it as `MAILUMI_API_KEY`.
5.  Create a webhook for the `email.received` event.
6.  Set the webhook URL to:

``` text
https://YOUR_PUBLIC_CUSTOMMAIL_HOST/api/webhooks/mailumi
```

7.  Apply a domain filter if Mailumi supports it.
8.  Copy the webhook signing secret into `MAILUMI_WEBHOOK_SECRET`.
9.  Send a test message to an existing CustomMail mailbox and inspect
    the webhook delivery result.

The webhook must reach the public application URL over HTTPS. A
successful webhook response indicates that the endpoint accepted the
event; verify that the message appears in the intended inbox as well.

### 3. Configure outbound sending

1.  Create a separate Mailumi API key with `email:send` permission.
2.  Restrict the key to the verified sending domain if the provider
    supports domain restrictions.
3.  Store it as `MAILUMI_SEND_API_KEY`.
4.  Confirm that the sender address belongs to the verified domain.
5.  Send a test message to an external mailbox.
6.  Check the CustomMail send status and Mailumi activity/logs.

The application submits outbound messages to:

``` text
POST https://mailumi.com/api/v1/emails
```

The API key is sent as a Bearer token from the server. Never expose it
in browser JavaScript.

A response with `status: queued` means the provider accepted the request
for processing, not that final delivery is guaranteed.

------------------------------------------------------------------------

## DNS configuration

DNS records are provider-specific. Use the exact values displayed in
your Mailumi dashboard.

At minimum, configure:

-   Domain verification TXT record(s).
-   DKIM CNAME record(s) or other DKIM records specified by Mailumi.
-   MX record(s) for inbound receiving, pointing to the Mailumi-provided
    destination.
-   SPF TXT record if required by Mailumi for outbound sending.
-   DMARC TXT record for domain-level email authentication and
    reporting.

### Important DNS rules

-   Do not replace existing root-domain MX records unless you intend to
    move all mail for that root domain.
-   If using a subdomain such as `mailus.aclclouds.online`, configure
    mail records on that subdomain.
-   Do not create multiple conflicting SPF records at the same hostname.
    Merge authorized senders into one SPF record according to your
    provider's instructions.
-   DKIM hostnames and values must match exactly.
-   Start with a DMARC policy appropriate for your rollout; monitor
    reports before enforcing a strict policy.
-   DNS changes may take time to propagate.

------------------------------------------------------------------------

## Deploy on InstaBox.sh

This project is designed to run on InstaBox.sh using Docker Compose. The
exact box ID, paths, secret commands, and public URL are specific to
each deployment.

### 1. Prepare the project

Clone the repository inside your InstaBox workspace or transfer the
project files into the workspace:

``` bash
git clone git@github.com:YOUR_GITHUB_USERNAME/custommail.git
cd custommail
```

For a private GitHub repository, configure GitHub SSH access on the box
before cloning. Never copy a private SSH key into the repository.

### 2. Configure persistent storage

The production Compose configuration should persist:

-   PostgreSQL data directory.
-   CustomMail message and attachment storage.

The deployed configuration used host storage under `/data/custommail`,
with PostgreSQL data in a dedicated subdirectory. Preserve the actual
paths and volume mappings in your working `docker-compose.yml`.

Do not store the database only inside a disposable container filesystem.

### 3. Configure secrets and environment

Create the production secrets in InstaBox's secret manager and ensure
the application container receives the environment variables listed in
[Configuration](#configuration).

Do not commit `.env`, paste secrets into issue trackers, or print secret
values in terminal output.

### 4. Check Compose configuration

From the project directory:

``` bash
docker compose config --quiet
```

If this reports missing variables or invalid YAML, correct the
configuration before building.

### 5. Build the application

``` bash
docker compose build app
```

### 6. Start the services

For a first deployment, after verifying the database volume and
environment configuration:

``` bash
docker compose up -d
```

For an application-only update where the database is already running,
use the deployment procedure appropriate to your current InstaBox setup.
Do not remove or recreate the PostgreSQL volume.

### 7. Check container health

``` bash
docker compose ps
```

Inspect application logs:

``` bash
docker compose logs --tail=100 app
```

Inspect database logs if needed:

``` bash
docker compose logs --tail=100 db
```

The database service name may differ; use the service name in your
Compose file.

### 8. Verify HTTP endpoints

From a machine that can reach the public application:

``` bash
curl -i https://YOUR_PUBLIC_CUSTOMMAIL_HOST/health
```

Then check the Temp Mail page:

``` bash
curl -I https://YOUR_PUBLIC_CUSTOMMAIL_HOST/temp-mail
```

The expected result is a successful HTTP response. Also open the
application in a browser and test login, inbox loading, and temporary
inbox creation.

### 9. Configure public access

Use InstaBox's public ingress/port mapping to expose the application
port configured in Compose (the application listens on port `8000` in
this project). Set `APP_BASE_URL` to the public HTTPS URL provided by
InstaBox.

Do not expose PostgreSQL publicly. Only the web application should be
reachable from the public internet.

------------------------------------------------------------------------

## Run locally for development

1.  Clone the repository.
2.  Create a virtual environment.
3.  Install dependencies.
4.  Create a private `.env` from `.env.example`.
5.  Configure a local database or use the test configuration.
6.  Start the FastAPI application.

Example:

``` bash
git clone https://github.com/YOUR_GITHUB_USERNAME/custommail.git
cd custommail

python3.11 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` with local-only values. For a local HTTP-only development
session, set `COOKIE_SECURE=false` if required by the current
configuration. Do not use that setting in production HTTPS deployments.

Start the development server:

``` bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

``` text
http://127.0.0.1:8000
```

Real inbound webhooks require a publicly reachable HTTPS endpoint. Local
development can test application logic and signed webhook tests without
exposing a development server publicly.

------------------------------------------------------------------------

## Using CustomMail

### Standard mailbox

1.  Open the public CustomMail URL.
2.  Sign in with the account provisioned for your deployment.
3.  Open the inbox and select a message to read it.
4.  Use the compose interface to send a message.
5.  Check the displayed send status for provider submission results.

### Temporary inbox

1.  Open **Temp Mail** from the webmail interface or visit `/temp-mail`.
2.  Create a disposable address.
3.  Copy the address into the service that needs a temporary email.
4.  Refresh or wait for the inbox to update.
5.  Open a received message to read it.
6.  Delete a message or the temporary inbox when finished.

Temporary inboxes expire automatically after 24 hours. Access tokens are
intended to remain in the current browser session; clearing session
storage may mean the temporary inbox cannot be restored.

------------------------------------------------------------------------

## API routes

The application exposes web routes and JSON API routes. The following
are the principal routes implemented by this project; request schemas
and authentication requirements should be checked in `app/main.py`.

  ---------------------------------------------------------------------------------------------------
  Route                                                           Purpose
  --------------------------------------------------------------- -----------------------------------
  `GET /health`                                                   Application health check

  `GET /temp-mail`                                                Temporary mail frontend

  `POST /api/webhooks/mailumi`                                    Receives and verifies Mailumi
                                                                  inbound webhook events

  `POST /api/temp-mailboxes`                                      Creates a temporary mailbox

  `GET /api/temp-mailboxes/current`                               Gets the current temporary mailbox

  `GET /api/temp-mailboxes/current/messages`                      Lists messages in the current
                                                                  temporary mailbox

  `GET /api/temp-mailboxes/current/messages/{message_id}`         Reads a temporary mailbox message

  `DELETE /api/temp-mailboxes/current/messages/{message_id}`      Deletes a temporary mailbox message

  `GET /api/temp-mailboxes/current/attachments/{attachment_id}`   Downloads an attachment

  `DELETE /api/temp-mailboxes/current`                            Deletes the current temporary
                                                                  mailbox
  ---------------------------------------------------------------------------------------------------

Other authentication, mailbox, message, and compose routes are
implemented in `app/main.py`. Consult that file for the exact paths and
payloads for the version you are running.

### Webhook verification

The Mailumi webhook endpoint validates the signature against the raw
request body and configured signing secret, using the provider's webhook
signature format and timestamp tolerance. Invalid signatures should be
rejected. Keep the webhook secret private and rotate it if it is
exposed.

------------------------------------------------------------------------

## Tests

Install dependencies, then run:

``` bash
python -m pytest -q
```

To run a specific test module:

``` bash
python -m pytest -q tests/test_inbound.py
python -m pytest -q tests/test_outbound.py
python -m pytest -q tests/test_temp_mail.py
```

The test suite covers core application behavior, inbound processing,
outbound integration behavior, and temporary mailbox behavior. Tests
should use test credentials and isolated test databases. Do not point
tests at the production database.

Before deploying a change:

1.  Run the full test suite.
2.  Build the Docker image.
3.  Run tests against the built image where practical.
4.  Back up persistent data.
5.  Deploy the application container.
6.  Check health and logs.
7.  Test one inbound and one outbound email.

------------------------------------------------------------------------

## Backups and recovery

The repository includes `ops/backup.sh`. Review the script before
running it and confirm the destination has sufficient free space.

Back up both:

-   PostgreSQL database data.
-   CustomMail message and attachment storage.

Keep backups outside the application source directory and protect them
because they may contain private email content and personal information.

A backup is useful only if it can be restored. Periodically test
restoration into an isolated environment. Do not delete existing backups
until a newer backup has been verified.

**Never run destructive commands such as `docker compose down -v`,
remove the PostgreSQL data directory, or prune volumes as a routine
deployment step.** These actions can permanently delete mail and account
data.

------------------------------------------------------------------------

## Security notes

-   Use HTTPS for all public access.
-   Use strong, unique admin passwords and change any default or
    previously exposed password.
-   Use separate secrets for application signing, webhook verification,
    inbound API access, and outbound API access.
-   Give API keys the minimum permissions needed.
-   Never expose Mailumi API keys in frontend code.
-   Do not commit `.env`, database files, message storage, backups,
    certificates, private keys, or tokens.
-   Restrict PostgreSQL to the private application network.
-   Keep the host, Python dependencies, Docker image, and application
    updated.
-   Limit access to backups and logs.
-   Do not log message bodies, passwords, API keys, or webhook secrets.
-   Temporary inboxes are disposable and should not be treated as
    private long-term storage.
-   Review attachment handling and file-size limits before accepting
    mail at scale.
-   Monitor provider delivery events; a queued message is not a delivery
    receipt.
-   Set up SPF, DKIM, and DMARC according to the DNS and sending
    provider instructions.

------------------------------------------------------------------------

## Troubleshooting

### Application does not start

``` bash
docker compose ps
docker compose logs --tail=150 app
```

Check environment variables, database connectivity, image build output,
and volume permissions. Do not paste secret values into support
requests.

### Database connection fails

-   Confirm the PostgreSQL container is healthy.
-   Confirm app and database share the intended private Docker network.
-   Confirm `DATABASE_URL` uses the Compose database service name and
    correct credentials.
-   Confirm the PostgreSQL data directory is mounted persistently.

### Mailumi webhook returns an error

-   Confirm the webhook URL is the current public HTTPS URL.
-   Confirm the `email.received` event is enabled.
-   Confirm the webhook signing secret matches `MAILUMI_WEBHOOK_SECRET`.
-   Confirm the domain is verified and inbound receiving is enabled.
-   Inspect application logs and Mailumi webhook delivery logs.
-   Do not disable signature verification to make a webhook work.

### Inbound message does not appear

-   Confirm the recipient domain has the correct Mailumi MX record.
-   Confirm the recipient address maps to an active CustomMail mailbox
    or temporary mailbox.
-   Check Mailumi webhook delivery status and HTTP response.
-   Check application logs and database health.
-   Send a new test message after correcting the configuration.

### Outbound email is not sent

-   Confirm `MAILUMI_SEND_API_KEY` is configured in the running app
    container.
-   Confirm the key has `email:send` permission.
-   Confirm the sender domain is verified and allowed by Mailumi.
-   Check the application send status and Mailumi API activity.
-   A queued response is not proof of final delivery; inspect provider
    delivery events and recipient spam folders.

### Temp Mail page does not load or create an inbox

-   Hard-refresh the browser.
-   Check that `/temp-mail`, `/static/temp-mail.js`, and
    `/static/temp-mail.css` return successful HTTP responses.
-   Open browser developer tools and inspect Console and Network errors.
-   Confirm the API routes under `/api/temp-mailboxes` respond
    successfully.
-   Check application logs for rate-limit or database errors.

### Browser shows an old interface

-   Hard-refresh the page (Ctrl+Shift+R).
-   Check the response content and status for the HTML, CSS, and
    JavaScript files.
-   Clear site cache only if needed; avoid clearing session storage if
    you need to retain access to an active temporary inbox.

------------------------------------------------------------------------

## Limitations

-   External email depends on Mailumi account capabilities, domain
    verification, DNS, provider limits, and recipient mail-server
    policies.
-   Mailumi accepting an outbound request does not guarantee inbox
    delivery.
-   DNS propagation is not instantaneous.
-   Temporary inboxes are intentionally short-lived and are not intended
    for sensitive or permanent use.
-   The exact InstaBox CLI and secret-management commands depend on the
    current platform interface.
-   This README documents the project architecture and intended
    configuration; verify exact environment defaults and route payloads
    against the checked-out source code before making production
    changes.

------------------------------------------------------------------------

## Contributing

1.  Fork the repository or create a feature branch.
2.  Keep credentials and production data out of commits.
3.  Add or update tests for behavior changes.
4.  Run `python -m pytest -q`.
5.  Open a pull request describing the change and test results.

Suggested branch workflow:

``` bash
git checkout -b feature/your-change
# Make changes and run tests
git add .
git commit -m "Describe the change"
git push -u origin feature/your-change
```

------------------------------------------------------------------------

## License

No license is declared in this repository yet. Unless a license is
added, the default copyright rules apply and others do not automatically
receive permission to reuse, modify, or distribute the project.

------------------------------------------------------------------------

## Maintainer

Repository: `https://github.com/thanush0/custommail`

For bug reports, include the relevant error message, steps to reproduce,
and application version/commit. **Remove email addresses, message
contents, passwords, API keys, tokens, and other private data before
sharing logs.**
