# Nginx proxy script

Keep Certbot TLS downloads pinned to a verified source revision. When updating
them, verify both URLs and test missing, empty, existing, and failed downloads
without modifying the host's `/etc` or restarting its services.
