# Működési dokumentáció

Ez a dokumentum a repository jelenlegi kódját írja le. A konténeres telepítés
lépései a `docs/containerization.md` fájlban találhatók.

## Bejövő API-kérés

A FastAPI alkalmazás jelenleg egy üzleti végpontot biztosít:

```text
POST /asset_predict
```

A végpont `X-API-Key` fejlécet vár. A kulcs elvárt értékét az
`INBOUND_API_KEY` környezeti változó adja meg. Hiányzó vagy hibás kulcs esetén
az API `401 Unauthorized`, hibás kérés esetén `400 Bad Request` választ ad.

A kérés mezői:

| Mező | Típus | Szabály |
| --- | --- | --- |
| `workorder_id` | pozitív egész | kötelező |
| `asset_id` | pozitív egész | a kódban `cmms_asset_id` néven kezelt külső CMMS-azonosító |
| `failure_cause_id` | pozitív egész vagy `null` | nem preventív munkalapnál kötelező |
| `failure_date` | ISO 8601 dátum-idő | ha későbbi az `ended` értékénél, az üzenet mentésre kerül, de nincs predikció |
| `ended` | ISO 8601 dátum-idő | kötelező |
| `type` | `PREVENTIVE` vagy `CORRECTIVE` | kötelező |
| `operation_ids` | egészek listája | opcionális; hiányzó vagy üres lista esetén nincs predikció |

A sikeres kérés `202 Accepted` választ ad:

```json
{
  "job_id": 123
}
```

## Idempotens sorba állítás

Az API a normalizált JSON-kérésből determinisztikus SHA-256 hash-t készít, majd
ezzel keres a `public.prediction_jobs` táblában.

- új, műveleteket tartalmazó kérés: új `queued` rekord jön létre;
- új, `operation_ids` nélküli vagy üres listás kérés: a worker lekéri és elmenti a CMMS-adatokat, majd predikció indítása nélkül `skipped` állapotra vált; az ok az `error_message` mezőbe kerül;
- új, `ended` utáni `failure_date` értékű kérés: új `skipped` rekord jön létre, amelyet a worker nem dolgoz fel; az ok az `error_message` mezőbe kerül;
- azonos `queued`, `processing`, `done` vagy `skipped` kérés: a meglévő `job_id` tér vissza;
- azonos `error` vagy `not_found` kérés: ugyanaz a rekord újra `queued` lesz.

Két egyidejű, azonos kérésből a `request_hash` egyedi indexe miatt csak egy job
jöhet létre.

## Worker-folyamat

A worker másodpercenként keres feldolgozható feladatot. A legrégebbi `queued`
sort `FOR UPDATE SKIP LOCKED` lekérdezéssel foglalja le, ezért több worker is
futhat anélkül, hogy ugyanazt a jobot egyszerre dolgoznák fel.

A fő lépések:

1. A job `processing` állapotba kerül.
2. A worker újra validálja az eltárolt payloadot.
3. A CMMS-től lekéri az eszköz hibaokait.
4. A helyi `assets.cmms_asset_id` alapján feloldja a belső `asset_id` értéket; ha az eszköz még nem létezik, automatikusan létrehozza.
5. Szinkronizálja a hibaokokat és a munkalap adatait. A CMMS
   `failure_causes[].operation_ids` elemei az `operations_done_lists` táblába
   kerülnek; az `/asset_predict` műveletei nem kerülnek ebbe a táblába.
6. Az `/asset_predict` műveleteiből hibaokonként JSON-listát készít, kizárólag
   az adott CMMS-hibaokhoz tartozó műveletekkel, majd meghívja a predikciós
   modult.
7. A predikciós modul elmenti az eszközszintű és a hibaoktípus-szintű
   idősorokat, majd a worker ellenőrzi az eszközszintű eredményt.
8. Két eredményt küld a CMMS felé.
9. A job `done`, `skipped`, `not_found` vagy `error` állapotba kerül.

A predikció eredményét a worker bontja két CMMS-payloadra. Az
`/dt-api/asset_prediction` az eszköz külső `asset_id` értékét és az összesített
`predicted_reliability` értéket kapja. Az
`/dt-api/asset_failure_cause_prediction` hibaoklistájához a worker a predikciós
modul örökölt `failure_type_ids` mezőjében kapott belső
`asset_failure_type_id` értékeket oldja fel CMMS `asset_failurecause_id`
értékekre, és a hozzájuk tartozó valószínűséget `predicted_reliability` néven
küldi.

A CMMS a `default_occurrence_probability` értékét 0 és 99 közötti
százalékos skálán adja át. A szinkronizálás ezt 100-zal osztja, és az
`asset_failure_types` táblában 0 és 0,99 közötti valószínűségként tárolja.

A feldolgozás közben egy heartbeat 30 másodpercenként frissíti az
`updated_at` mezőt. A tíz perce heartbeat nélkül maradt `processing` feladatokat
a worker automatikusan újra sorba állítja. Ezt 30 másodpercenként ellenőrzi.

## CMMS-kapcsolat

A worker az alábbi hívásokat használja:

```text
GET  {CMMS_BASE_URL}/dt-api/asset_failure_causes/{asset_id}
POST {CMMS_BASE_URL}/dt-api/asset_prediction
POST {CMMS_BASE_URL}/dt-api/asset_failure_cause_prediction
```

Mindegyik kérés fejléce:

```text
x-api-key: <CMMS_TOKEN>
```

A teljes HTTP-timeout 10 másodperc, a kapcsolódási és olvasási timeout 5-5
másodperc. Ha a CMMS nem érhető el, az API és az adatbázis ettől még működhet,
de a worker nem tudja sikeresen befejezni a külső adatot igénylő jobokat.

## Jobállapotok

| Állapot | Jelentés |
| --- | --- |
| `queued` | feldolgozásra vár |
| `processing` | egy worker lefoglalta |
| `done` | a teljes feldolgozás és a CMMS POST-ok sikerültek |
| `not_found` | szükséges helyi vagy CMMS-adat hiányzik |
| `error` | validációs, predikciós, adatbázis- vagy CMMS-hiba történt |

Az aktuális állapotok lekérdezése:

```powershell
docker compose exec db psql -U dt_admin -d dt_db_cmms -c "SELECT job_id, workorder_id, status, error_message, updated_at FROM prediction_jobs ORDER BY job_id DESC LIMIT 20;"
```
