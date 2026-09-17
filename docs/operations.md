# Üzemeltetési útmutató

Minden parancsot a projekt gyökérkönyvtárából futtass PowerShellben.

## Indítás

Teljes rendszer:

```powershell
docker compose up --build -d
```

CMMS nélküli fejlesztői üzem, worker nélkül:

```powershell
docker compose up --build -d db db-init api
```

Állapotellenőrzés:

```powershell
docker compose ps -a
```

Elvárt állapotok:

- `db`: `Up ... (healthy)`;
- `api`: `Up ... (healthy)`;
- `db-init`: `Exited (0)`;
- `worker`: `Up`, ha a CMMS elérhető; különben szándékosan leállítható.

## Gyors működési ellenőrzés

```powershell
Invoke-WebRequest http://localhost:8000/docs -UseBasicParsing |
    Select-Object StatusCode

docker compose exec db pg_isready -U dt_admin -d dt_db_cmms
```

Az elvárt eredmény HTTP `200`, illetve `accepting connections`.

## Naplók

```powershell
docker compose logs --tail=100 api
docker compose logs --tail=100 worker
docker compose logs --tail=100 db
docker compose logs --tail=100 db-init
```

Folyamatos követéshez:

```powershell
docker compose logs -f worker
```

A követés `Ctrl+C`-vel megszakítható; ettől a háttérben futó konténerek nem
állnak le.

## Jobok ellenőrzése

```powershell
docker compose exec db psql -U dt_admin -d dt_db_cmms -c "SELECT job_id, workorder_id, status, error_message, created_at, updated_at FROM prediction_jobs ORDER BY job_id DESC LIMIT 20;"
```

Az `error_message` mutatja, hogy adat-, predikciós vagy CMMS-hiba történt-e.

## Újraindítás és leállítás

```powershell
# Egy szolgáltatás újraindítása
docker compose restart api

# Minden szolgáltatás leállítása, volume-ok megtartásával
docker compose down

# Ismételt indítás rebuild nélkül
docker compose up -d
```

Kód- vagy dependency-változtatás után:

```powershell
docker compose up --build -d
```

## Konfiguráció módosítása

A `.env` változtatása után hozd létre újra az érintett containereket:

```powershell
docker compose up -d --force-recreate api worker
```

A `POSTGRES_PASSWORD` megváltoztatása meglévő volume mellett nem módosítja
automatikusan az adatbázisban tárolt jelszót. Ilyenkor SQL-lel kell jelszót
váltani, vagy csak eldobható fejlesztői adatoknál új volume-ot létrehozni.

## Sémaváltozás

A `db/init/001_schema.sql` csak üres adatvolume első inicializálásakor fut. Egy
már működő adatbázison ne a fájl átírásától várd a változást: készíts migrációt,
mentsd az adatbázist, majd előbb tesztkörnyezetben próbáld ki.

A `db-init` az elavult predikciós hypertable-sémát automatikusan lecseréli, ha
a három predikciós tábla üres. Ha bármelyikben adat található, a szolgáltatás
szándékosan hibával leáll, hogy ne törölhessen predikciós előzményt. Ilyenkor
előbb mentés és külön adatmigráció szükséges.

## Backup

```powershell
New-Item -ItemType Directory -Force backups
docker compose exec db pg_dump -U dt_admin -d dt_db_cmms -Fc -f /tmp/dt_db_cmms.dump
docker compose cp db:/tmp/dt_db_cmms.dump ./backups/dt_db_cmms.dump
```

A fájl tartalmának ellenőrzése:

```powershell
docker compose cp ./backups/dt_db_cmms.dump db:/tmp/verify.dump
docker compose exec db pg_restore --list /tmp/verify.dump |
    Select-Object -First 30
```

A teljes, TimescaleDB-specifikus teszt-visszaállítási folyamat a
`docs/containerization.md` fájlban található.

## Adattörlés

```powershell
docker compose down --volumes
```

Ez törli a Compose-projekthez tartozó adatbázis- és predikciós volume-okat. A
parancs visszafordíthatatlan, ezért csak ellenőrzött backup után vagy eldobható
fejlesztői adatoknál használd.
