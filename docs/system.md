# Rendszeráttekintés

## Architektúra

```text
CMMS / kliens
      |
      | POST /asset_predict + X-API-Key
      v
 FastAPI (api) -----> PostgreSQL + TimescaleDB (db)
                           ^
                           |
                    queue worker (worker)
                           |
                           +---- GET/POST ----> CMMS
```

A Compose egy közös belső hálózatot hoz létre. Az alkalmazás ebből a hálózatból
`db:5432` címen éri el az adatbázist. A host gépen publikált adatbázis-port csak
adminisztrációhoz szükséges, és `127.0.0.1` címre van korlátozva.

## Konténerek

| Szolgáltatás | Feladat | Életciklus |
| --- | --- | --- |
| `db` | PostgreSQL 16 és TimescaleDB | folyamatos, healthcheckkel |
| `db-init` | a séma teljességének ellenőrzése | egyszer fut, siker esetén `Exited (0)` |
| `api` | FastAPI/Uvicorn a 8000-es belső porton | folyamatos, healthcheckkel |
| `worker` | a tartós adatbázis-queue feldolgozása | folyamatos; CMMS-t igényel |

Az `api` és a `worker` ugyanabból a `digital-twin-simple:local` image-ből fut,
de más indítóparancsot kap.

## Indítási sorrend

1. A `db` elindul.
2. Üres `postgres_data` volume esetén lefut a `db/init/001_schema.sql`.
3. A PostgreSQL healthcheck sikeres lesz.
4. A `db-init` ellenőrzi a sémát.
5. Csak sikeres `db-init` után indul az `api` és a `worker`.

Ez a sorrend nem időzítőkön alapul: a Compose healthcheck- és
`service_completed_successfully` feltételeket használ.

## Adatperzisztencia

- `postgres_data`: az adatbázis tartós fájljai;
- `prediction_data`: a `/data/predictions` könyvtár közös, fájlkimenetek számára
  fenntartott volume-ja az API és a worker számára.

A containerek újraépítése nem törli a volume-okat. A
`docker compose down --volumes` viszont igen.

## Adatbázis

- adatbázis: alapértelmezetten `dt_db_cmms`;
- tulajdonos/felhasználó: alapértelmezetten `dt_admin`;
- alkalmazásséma: `public`;
- TimescaleDB hypertable-ek:
  - `asset_worksheet_lists`;
  - `measurements`;
  - `kpi_values`.

A `predictions`, `prediction_asset_levels` és
`prediction_asset_failure_type_levels` normál PostgreSQL-táblák. A predikció
időhorizontja a `predictions` rekordban található; a két eredménytábla ehhez a
rekordhoz kapcsolódik.

A séma SQL-alapú inicializálása csak üres volume első indulásakor történik meg.
Már létező adatbázis módosításához verziózott migráció szükséges; az Alembic
függőség már szerepel a projektben, de a migrációs környezet még nincs kiépítve.

## Biztonsági határok

- A bejövő API-t az `INBOUND_API_KEY` védi.
- A futás közbeni asset-mapping végpontot a külön `MAPPING_ADMIN_API_KEY` védi.
- A CMMS-kimenő hívások a külön `CMMS_TOKEN` kulcsot használják.
- A `.env` fájl nincs Gitben és nem kerül be az image-be.
- Az alkalmazás-image nem root Linux-felhasználóként fut.
- Az adatbázis-port csak a host loopback címére van publikálva.

Fejlesztéshez ez megfelelő alap, de éles rendszerben titokkezelő, TLS/reverse
proxy, korlátozott adatbázis-jogosultságok, monitorozás és automatizált backup is
szükséges.

## Fontos forrásfájlok

| Fájl | Szerep |
| --- | --- |
| `compose.yaml` | szolgáltatások, hálózat, volume-ok, healthcheckek |
| `Dockerfile` | Python alkalmazás-image |
| `.env.example` | konfigurációs sablon titkok nélkül |
| `db/init/001_schema.sql` | induló TimescaleDB-séma |
| `app/init_db.py` | sémaellenőrzés |
| `app/settings.py` | közös környezeti konfiguráció |
| `app/maintenance/api.py` | FastAPI végpont |
| `app/maintenance/asset_mapping.py` | futás közbeni CMMS–DC mappingek kezelése |
| `app/maintenance/jobs.py` | idempotens sorba állítás |
| `app/maintenance/job_queue.py` | foglalás, heartbeat és újra sorba állítás |
| `app/maintenance/worker.py` | feldolgozó folyamat |
| `app/maintenance/data_sync.py` | CMMS- és munkalapadatok szinkronizálása |
| `config/prediction.toml` | verziókezelt predikciós működési paraméterek |
| `config/datacollector.toml` | verziókezelt adatgyűjtési működési paraméterek |
