# Production Apache2 Setup

## Architecture

```
Internet → Apache2 (443 HTTPS)
             ├─ /DriverQ/*   → proxy → 127.0.0.1:8000  (FastAPI: API + camera images)
             └─ /*           → proxy → 127.0.0.1:3000  (Next.js frontend)
```

`NEXT_PUBLIC_API_BASE_URL=/DriverQ` is baked into the JS bundle at build time as a
path-only prefix. The browser constructs same-origin API calls such as
`/DriverQ/api/…`, which Apache strips before forwarding to the FastAPI container.
No hostname is embedded in the bundle, so the build is host-independent.

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

    # API + camera images (strip /DriverQ prefix before forwarding)
    ProxyPass        /DriverQ/ http://127.0.0.1:8000/
    ProxyPassReverse /DriverQ/ http://127.0.0.1:8000/

    # Next.js frontend
    ProxyPass        / http://127.0.0.1:3000/
    ProxyPassReverse / http://127.0.0.1:3000/
</VirtualHost>
```

Enable and reload:

```bash
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