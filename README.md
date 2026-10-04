# Reroute

Reroute is a local-first Warsaw transit control room. Explore public transport routes and live vehicle positions, then simulate how a disruption could affect lines and stops.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

Open the application at <http://localhost:5173> and the API documentation at <http://localhost:8000/docs>. The demo network works without an API key. Add a Warsaw API key to `.env` to enable live vehicle positions.

## Data and attribution

- Warsaw public transport schedules, routes and stops are provided by ZTM Warsaw. The GTFS feed is a community conversion of the official schedule data and is refreshed daily: <https://mkuran.pl/gtfs/>.
- Live bus and tram positions are provided by the City of Warsaw open-data API: <https://dane.um.warszawa.pl/>. A self-registered API key is required.
- Base map © OpenStreetMap contributors. Map tiles are loaded from `tile.openstreetmap.org`; attribution and caching requirements apply: <https://operations.osmfoundation.org/policies/tiles/>.

Live position and disruption impact are estimates based on the available feed. They are not official arrival predictions or passenger counts.

## Development

Source code, comments and technical documentation are in English. The interface supports Polish and English.
