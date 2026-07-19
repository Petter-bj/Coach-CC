# Dashboard

Dette er den mobil-første dashboard-klienten for «I dag».

Når den åpnes som en enkel statisk preview, bruker den representative
eksempeldata. Når den serveres av `src.api.app`, henter den ekte, read-only
data fra `/api/today` på samme private URL. Chat, review-lagring og
Garmin-push er fortsatt bevisst ikke koblet på.

Start det fra prosjektmappen:

```bash
python3 -m http.server 4173 --directory dashboard_preview
```

Åpne deretter [http://localhost:4173](http://localhost:4173) på Mac-en. For å prøve det på iPhone, bruk samme adresse med Mac-ens lokale IP mens begge er på samme Wi-Fi.

På VPS-en serveres de samme filene av `trening-api.service`; Tailscale Serve
blir da den eneste inngangen fra telefonen.
