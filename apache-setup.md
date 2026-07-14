# Production Apache2 Setup

## Architecture

From the **browser's perspective**, the entire site is a single origin: `https://wiselab.uwaterloo.ca`.
Apache internally routes different paths to different backend containers:

```
Internet → Apache2 (443 HTTPS) — single origin: https://wiselab.uwaterloo.ca
             │
             ├─ /DriverQ/*        → proxy → 127.0.0.1:3000/DriverQ/  (Next.js frontend)
             ├─ /DriverQServer/*  → proxy → 127.0.0.1:8000           (FastAPI: API + camera images)
             └─ /...  →             other sites (untouched, served as before)
```

Both `/DriverQ/` and `/DriverQServer/` use explicit `ProxyPass` prefixes rather than
a catch-all `ProxyPass /`. This is deliberate: this host serves other, unrelated sites
from the same Apache instance. A catch-all `ProxyPass /` would
intercept every path not otherwise matched — including those other sites — and
incorrectly forward them to the Next.js container. Explicit prefixes for both DriverQ
services ensure no interference with existing or future sites on this host.

`/DriverQ` is used for the frontend since it is the primary, user-facing URL that
people will bookmark and share. `/DriverQServer` is used for the API so the two
prefixes remain visually distinct and unambiguous in logs and Apache config.

Because all requests are same-origin, CORS is not required. The browser's Same-Origin
Policy does not block requests between `/DriverQServer/api/…` and `/DriverQ/` since
they share the same scheme, host, and port.

`NEXT_PUBLIC_API_BASE_URL=/DriverQServer` is baked into the JS bundle at build time as a
path-only prefix. The browser constructs same-origin API calls such as
`/DriverQServer/api/…`, which Apache strips before forwarding to the FastAPI container.
No hostname is embedded in the bundle, so the build is host-independent.

`BASE_PATH=/DriverQ` is passed as a Next.js build arg (see
`frontend/next.config.ts`'s `basePath` option). Next.js automatically prefixes all
page routes, static assets (`/_next/...`), and internal links with this path, so the
app works correctly when served from a non-root path behind Apache.

**Why the API prefix is stripped but the frontend prefix is not:** FastAPI's routes
(`/api/scenes`, etc.) are plain, prefix-unaware paths, and JSON responses contain no
self-referencing URLs — Apache can safely strip `/DriverQServer/` with no downstream
effect. Next.js is different: it's a stateful SPA whose HTML/JS embeds root-relative
asset and route URLs (`/_next/...`, RSC payloads, `__NEXT_DATA__`). Those URLs are
generated *by Next.js itself* using its `basePath` config, so the prefix must reach
the Next.js server unchanged. Apache has no general way to rewrite paths embedded
inside HTML/JS response bodies (`ProxyPassReverse` only rewrites `Location`/redirect
headers), so stripping the frontend prefix would break every asset and internal
navigation request. This asymmetry is intentional, not an inconsistency.

### Virtual Host (`/etc/apache2/sites-available/driverq.conf`)

```apache
<VirtualHost *:80>
    ServerName wiselab.uwaterloo.ca
    RewriteEngine On
    RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]
</VirtualHost>

<VirtualHost *:443>
    ServerName wiselab.uwaterloo.ca

    SSLEngine on
    SSLCertificateFile    /etc/.../wiselab.uwaterloo.ca/fullchain.pem
    SSLCertificateKeyFile /etc/.../wiselab.uwaterloo.ca/privkey.pem

    # Security headers
    Header always set Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
    Header always set X-Frame-Options "SAMEORIGIN"
    Header always set X-Content-Type-Options "nosniff"
    Header always set Referrer-Policy "strict-origin-when-cross-origin"
    Header always set Permissions-Policy "geolocation=(), microphone=(), camera=()"

    # Content Security Policy
    # default-src 'none' denies everything not explicitly listed.
    # script-src: 'unsafe-inline' is required — Next.js App Router injects inline <script>
    #   chunks for RSC streaming hydration that cannot be hashed or nonced without
    #   adding Next.js middleware. The app renders no user-supplied HTML so XSS surface
    #   is low, and all external script origins are blocked.
    # style-src: 'unsafe-inline' is required — React inline style={{ }} props throughout
    #   the components write inline style attributes.
    # connect-src 'self': strict — all API calls are same-origin after CORS removal.
    #   This is the highest-value directive: injected scripts cannot exfiltrate data.
    # frame-ancestors 'self': modern replacement for X-Frame-Options (both are set for
    #   full browser coverage).
    Header always set Content-Security-Policy "\
default-src 'none'; \
script-src 'self' 'unsafe-inline'; \
style-src 'self' 'unsafe-inline'; \
img-src 'self'; \
connect-src 'self'; \
font-src 'self'; \
worker-src 'none'; \
object-src 'none'; \
base-uri 'self'; \
frame-ancestors 'self';"

    ProxyPreserveHost On
    ProxyRequests Off

    # API + camera images: STRIP the /DriverQServer prefix before forwarding.
    # FastAPI's routes (e.g. /api/scenes) have no knowledge of any prefix, and
    # JSON responses don't embed self-referencing URLs, so stripping is safe.
    ProxyPass        /DriverQServer/ http://127.0.0.1:8000/
    ProxyPassReverse /DriverQServer/ http://127.0.0.1:8000/

    # Next.js frontend: PRESERVE the /DriverQ prefix (do not strip).
    # Next.js's basePath config (frontend/next.config.ts) makes the app itself
    # prefix-aware: it emits /DriverQ/... in its HTML, JS chunk URLs, and RSC
    # payloads. If Apache stripped the prefix, Next.js would render assuming it
    # lives at "/", the browser would then request unprefixed asset URLs
    # (e.g. /_next/...) directly from Apache, and those wouldn't match this
    # ProxyPass rule. Apache cannot rewrite paths embedded in HTML/JS bodies
    # (ProxyPassReverse only rewrites Location/redirect headers), so the prefix
    # must be forwarded unchanged and handled internally by Next.js instead.
    ProxyPass        /DriverQ/ http://127.0.0.1:3000/DriverQ/
    ProxyPassReverse /DriverQ/ http://127.0.0.1:3000/DriverQ/
</VirtualHost>
```

Enable and reload:

```bash
# ensure required modules are enabled
sudo a2enmod headers proxy_http
sudo a2ensite driverq.conf
sudo apache2ctl configtest
sudo systemctl reload apache2
```

### Global hardening (`/etc/apache2/conf-available/security.conf`)

These are server-wide settings — they must go in `apache2.conf` or a file loaded
from `conf-enabled/`, **not** inside a `<VirtualHost>` block:

```apache
# Suppress the Apache version from the Server: response header
ServerTokens Prod

# Remove the Apache version footer from error pages
ServerSignature Off
```

Enable and reload:

```bash
sudo a2enconf security
sudo apache2ctl configtest
sudo systemctl reload apache2
```

### Firewall

Open only the ports Apache needs. The app ports (8000, 3000) do **not** need explicit `deny` rules:

```bash
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

`docker-compose.yml` binds the app containers to `127.0.0.1` (`"127.0.0.1:8000:8000"` and `"127.0.0.1:3000:3000"`), so ports 8000 and 3000 are only reachable from the local machine — they are never exposed on any network interface. An external port scan cannot detect them; there is nothing for UFW to block.

> **Note:** On standard root Docker, `ufw deny` rules for published ports are ineffective anyway — Docker inserts raw `iptables` rules that are evaluated before UFW's chains. This deployment uses rootless Docker (where UFW rules *do* apply), but the loopback binding makes them redundant in either case. The correct defence is the `127.0.0.1` binding, not a firewall rule.