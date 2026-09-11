# digital_twin_simple

A projekt karbantartási munkalapok fogadására és eszközmegbízhatósági
predikciók aszinkron feldolgozására készült. A FastAPI alkalmazás a beérkező
kérést PostgreSQL-adatbázisban sorba állítja, a külön worker pedig szinkronizálja
a szükséges CMMS-adatokat, meghívja a predikciós modult, eltárolja az eredményt,
majd visszaküldi azt a CMMS felé.

## A rendszer részei

- `api`: FastAPI, amely ellenőrzi és sorba állítja a kéréseket;
- `worker`: a `prediction_jobs` sor feldolgozója;
- `datacollector`: a SilverFrog DC metaadatainak és méréseinek időzített
  betöltője;
- `db`: PostgreSQL 16 + TimescaleDB;
- `db-init`: induláskor ellenőrzi az adatbázis tábláit, a TimescaleDB bővítményt, a
  `jobstatus` enumot és a hypertable-öket.

Az adatbázis objektumai a `public` sémában vannak. Az inicializáló séma:
`db/init/001_schema.sql`.

## Gyors indítás Dockerrel

Feltételek:

- Docker Desktop futó Docker Engine-nel;
- Docker Compose;
- szabad API-port, alapértelmezés szerint `8000`;
- szabad adatbázis-port, alapértelmezés szerint `5432`.

PowerShellben, a projekt gyökérkönyvtárából:

```powershell
Copy-Item .env.example .env
```

Nyissa meg a létrejött `.env` fájlt, és cserélje le a
`POSTGRES_PASSWORD`, `INBOUND_API_KEY` és `MAPPING_ADMIN_API_KEY` értékét.

A teljes rendszer indítása:

```powershell
docker compose up --build -d
docker compose ps -a
```

Ha a CMMS még nem érhető el, a workert hagyja leállítva:

```powershell
docker compose stop worker
```

Az API dokumentációja: <http://localhost:8000/docs>

Gyors ellenőrzés:

```powershell
Invoke-WebRequest http://localhost:8000/docs -UseBasicParsing |
    Select-Object StatusCode
```

Az elvárt HTTP-státusz `200`. A `docker compose ps -a` kimenetében az `api` és
a `db` állapota `healthy`, a `db-init` állapota pedig `Exited (0)` legyen. A
`db-init` nem folyamatos szolgáltatás: a sikeres nullás kilépés az elvárt működés.

## API használata

Az üzleti predikciós végpont:

```text
POST /asset_predict
```

A futás közbeni CMMS–DC mapping adminisztrációs végpontja:

```text
POST /sf_asset_mapping
```

Az `/asset_predict` kéréshez kötelező az alábbi fejléc:

```text
X-API-Key: <az INBOUND_API_KEY értéke>
```

A `/sf_asset_mapping` végpont ugyanezt a fejlécnevet használja, de a külön
`MAPPING_ADMIN_API_KEY` értékével.

Példakérés PowerShellből:

```powershell
$headers = @{ "X-API-Key" = "change-this-api-key" }
$body = @{
    workorder_id    = 1
    asset_id        = 1
    failure_cause_id = 1
    failure_date    = "2026-07-30T08:00:00"
    ended           = "2026-07-30T09:00:00"
    type            = "CORRECTIVE"
    operation_ids   = @(1)
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri http://localhost:8000/asset_predict `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
```

Sikeres fogadáskor az API `202 Accepted` választ és egy `job_id` értéket ad
vissza. Hiányzó vagy üres `operation_ids` esetén a worker előbb szinkronizálja
a CMMS-adatokat, majd a job `skipped` állapotba kerül, a kihagyás oka bekerül az
`error_message` mezőbe, és predikció nem indul. Ha a `failure_date` későbbi,
mint az `ended`, a job már CMMS-szinkronizálás nélkül `skipped` állapotban
kerül mentésre. Azonos tartalmú kérés nem hoz létre új sort: a rendszer a kérés SHA-256
hash-e alapján a meglévő feladat azonosítóját adja vissza.

## Hasznos parancsok

```powershell
# Állapot
docker compose ps -a

# Naplók
docker compose logs --tail=100 api
docker compose logs --tail=100 worker
docker compose logs --tail=100 db

# Leállítás, az adatok megtartásával
docker compose down

# Újraindítás
docker compose up -d
```

Az adatbázis közvetlen ellenőrzése:

```powershell
docker compose exec db psql -U dt_admin -d dt_db_cmms -c "\dt public.*"
docker compose exec db psql -U dt_admin -d dt_db_cmms -c "SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_schema='public' ORDER BY hypertable_name;"
```

## Fejlesztői futtatás Docker nélkül

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
uvicorn app.maintenance.api:app --host 0.0.0.0 --port 8000
```

A worker külön terminálban indítható:

```powershell
.\.venv\Scripts\Activate.ps1
python -m app.maintenance.worker
```

Docker nélküli futtatáskor a komponensenkénti `POSTGRES_*` változók helyett a
teljes `DATABASE_URL` és `ASYNC_DATABASE_URL` is megadható. Ha jelen vannak,
elsőbbséget élveznek.

## Dokumentáció

- `docs/containerization.md` – részletes Docker Compose telepítési,
  hibaelhárítási és mentés-visszaállítási útmutató;
- `docs/mukodes.md` – az API, a queue, a worker és a CMMS-adatfolyam működése;
- `docs/system.md` – rendszer- és komponensáttekintés;
- `docs/operations.md` – üzemeltetési ellenőrzőlista és gyakori parancsok;
- `docs/openapi.json` – API-leírás;
- `docs/cmms_get_calls_system.json` – CMMS-hívásokhoz kapcsolódó referencia.

## SilverFrog DC adatgyűjtés

A `datacollector` Docker-szolgáltatás a DC `/ex/api/metrics` és
`/ex/api/metric-values` GET végpontjairól tölti be a metaadatokat és a
méréseket. A kapcsolathoz a `.env` fájlban a következő értékeket kell megadni:

```env
DC_BASE_URL=https://dc.dev2.sfrog.hu
DC_API_KEY=replace-me
```

A lekérési időköz, az átfedés, a lapozás és a metrikák frissítési időköze a
`config/datacollector.toml` fájlban állítható. A predikció időablakai a
`config/prediction.toml` fájlban találhatók. Az elutasított adatok külön
forgó naplóba kerülnek a `datacollector_logs` Docker volume-ban.
Az `/ex/api/metric-values` lapozása nulláról indul; a datacollector a válasz
`content` listáját dolgozza fel, és a `page.number`/`page.totalPages` mezők
alapján kéri le a további oldalakat.

## CMMS–DC asset mapping

A CMMS `asset_id` és a DC `technicalObjectUniqueIdentifier` kapcsolatát a futó
API `POST /sf_asset_mapping` végpontján lehet betölteni. A végpontot a külön
`MAPPING_ADMIN_API_KEY` védi. JSON-fájl importálásához használható a
`scripts/import_asset_mappings.py` segédprogram. A részletes formátum és a
konfliktuskezelés a `docs/asset_mapping.md` dokumentumban található.

## Dashboard integráció

A külön `dt_dashboard` repository REST és WebSocket kapcsolaton keresztül éri el
ezt a backendet. A folyamatgráf, a szenzoronkénti mintavételezés és aggregáció, a
rendelések, a konkrét termékpéldányok, az NFC-tálcák, a KPI-k és az élő grafikon
részletes szerződése a `docs/dashboard.md` dokumentumban található.
