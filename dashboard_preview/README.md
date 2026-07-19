# Dashboard-preview

Dette er et statisk, lokalt design-preview. Det bruker bare eksempeldata og sender ingenting til Kimi eller VPS-en.

Start det fra prosjektmappen:

```bash
python3 -m http.server 4173 --directory dashboard_preview
```

Åpne deretter [http://localhost:4173](http://localhost:4173) på Mac-en. For å prøve det på iPhone, bruk samme adresse med Mac-ens lokale IP mens begge er på samme Wi-Fi.

Den ferdige dashboard-appen vil bruke de samme layout-ideene, men hente ekte data fra en privat API på VPS-en over Tailscale.
