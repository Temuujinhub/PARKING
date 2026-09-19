# TLS-ALPN certificate renewal and nginx recovery

The ALPN pre-hook stops nginx to release port 443. acme.sh installs the renewed
certificate and invokes its reload command before the success post-hook. If that
command is only `systemctl reload nginx`, the inactive service cannot reload and
the installation can return before the post-hook starts it. This sequence caused
a production outage on 2026-09-19 even though a start post-hook was configured.

Use the following certificate installation hook:

```sh
nginx -t && systemctl reload-or-restart nginx
```

The configuration check must succeed first. An active service reloads its
certificate; an inactive service starts with the installed certificate. The
existing pre-hook and post-hook remain stop and start, respectively.

Changing `setup_domain_alpn.sh` does not update existing domain configuration.
Inspect each installed domain's saved `Le_ReloadCmd`, back up its configuration,
and change only that hook. acme.sh may encode hook values using its base64 config
format; never source account/domain files in an audit or print private keys.
The two existing production domain hooks were updated and verified separately.

## Scheduled renewal guard

`deploy/parking-acme-renewal.sh` is the root cron entry point. Install it as
`/usr/local/sbin/parking-acme-renewal` with root ownership and mode 0700, then
replace only the existing ACME command in root's crontab, retaining its schedule
and every other job. Keep a private crontab/config backup and verify HTTPS after
installation. Installing the guard must not invoke renewal as a smoke test.

The guard:

- Shares `/run/lock/parking-deploy.lock` with application deployments. A busy
  lock skips the renewal attempt; the next existing cron occurrence retries.
- Checks that nginx is running and its configuration is valid before renewal.
- Limits the ACME process group to 300 seconds, with a further 10-second TERM
  grace period before KILL; this includes its standalone challenge listener.
- Starts nginx on ordinary failure/timeout if the renewal left it inactive.
  It preserves the ACME exit code and reports recovery failure separately.
- Writes a root-only local renewal log and brief journal messages tagged
  `parking-acme-renewal`; the journal does not contain private ACME output.

This guard is not zero-downtime renewal: TLS-ALPN still temporarily owns port
443. SIGKILL of the guard itself, host failure, or invalid nginx configuration
requires separate recovery. Audit the private log's retention with the server's
normal log policy. Do not disable another certificate client just because its
timer exists; inspect its configured renewal domains first.

## Verification

Unit tests reproduce the old reload-before-start failure and exercise success,
ACME failure, timeout, initial maintenance, invalid configuration and failed
nginx recovery with fake service commands. Production installation also verifies
saved hook values, cron contents, configuration syntax and HTTPS 200 without
forcing a certificate renewal. Observe the next scheduled real renewal before
claiming live end-to-end renewal acceptance.

References: [acme.sh source](https://github.com/acmesh-official/acme.sh/blob/master/acme.sh)
(`issue`, `_installcert`, `_on_issue_success`) and
[official hook options](https://github.com/acmesh-official/acme.sh/wiki/Options-and-Params).
