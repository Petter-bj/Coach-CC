# Dashboard client

This is the mobile-first client served by `src.api.app` in the private Coach-CC
deployment.

It contains four main surfaces:

- **I dag** — recovery signals, the current recommendation, planned workout,
  workout reviews, and a scoped daily coach conversation.
- **Uke** — week navigation, planned and completed sessions, day logs, and a
  week coach that can create explicit plan and Hevy-routine proposals.
- **Blokk** — longer training blocks with a separate planning conversation and
  explicit block proposals.
- **Coach** — a persistent free-form conversation with broader personal
  context. It can discuss goals and priorities, but changes still move through
  a visible proposal and confirmation flow.

When opened as a static preview, the client uses representative data and local
fallback responses. It does not store coach messages or contact data providers.

```bash
python3 -m http.server 4173 --directory dashboard_preview
```

Open [http://localhost:4173](http://localhost:4173) after starting the server.

In production, `trening-api.service` serves these files and the FastAPI API on
the same private Tailscale URL. The browser never receives an API or model key.
