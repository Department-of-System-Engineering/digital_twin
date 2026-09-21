# Dashboard integration

The React dashboard remains in the separate `dt_dashboard` repository. During
development it proxies `/api` HTTP and WebSocket traffic to this FastAPI
application. The dashboard's existing request and response shapes are preserved;
all joins and chart pivoting happen in the backend.

## Transport split

- REST serves process topology, assets, sensor metadata, chart history, products,
  orders and KPI values.
- `WS /ws/sensors/chart` serves live chart deltas in parallel with the
  historical REST query, so slow history loading cannot delay live updates.
- A chart opens a WebSocket only when its `toDate` is close to the browser's
  current server-local time. The dashboard allows 90 seconds by default because
  manually selected `datetime-local` values may be minute-aligned.
- The WebSocket emits the same `ChartData[]` shape as `POST /sensors/chart`.

The current implementation discovers measurements written by either Node-RED or
the backend by polling newly generated `measurement_id` values. MQTT-to-WebSocket
fan-out is deliberately outside the current scope.

## Time convention

All timestamps use the server's local clock and PostgreSQL
`TIMESTAMP WITHOUT TIME ZONE`. Producers and consumers must use the same server
clock convention. REST and WebSocket timestamps contain no `Z` or UTC offset.

## Sensor sampling and aggregation

`sensors.measurement_frequency` is the sensor-specific sampling interval in
seconds. The effective chart interval is:

```text
max(request.filter.samplingFrequency, sensors.measurement_frequency)
```

`sensors.chart_aggregation_method` controls each sensor independently and accepts
`average`, `latest`, `minimum` or `maximum`. Its default is `latest`.

Historical responses contain at most `DASHBOARD_MAX_CHART_POINTS` recent
buckets. Live polling also uses an explicit time predicate so TimescaleDB can
exclude old chunks from every poll.

## Process topology

`process_configurations` versions a complete topology. `routing` stores directed
edges, while `asset_process_steps` is a many-to-many mapping between assets and
process steps. `GET /process/graph` reads the active configuration and constructs
the dashboard node name as:

```text
process step name - asset name 1, asset name 2
```

Only one configuration can be active. Populate a replacement configuration
before activating it, then deactivate the old configuration and activate the new
one in the same database transaction.

## Orders and physical products

An order contains normalized `order_items`. Creating an order also creates one
`product_instances` row per requested physical product, in deterministic
`order_items.position` and `product_instances.sequence_number` order.

`POST /trays/{nfc_tag_id}/assign-next` assigns the scanned tray to the next queued
product of an order. The NFC tag is normalized to uppercase. Partial unique
indexes guarantee that a tray and a product have at most one active assignment.

`POST /products/{product_instance_id}/events` accepts `arrived`, `departed` and
`done`. A `done` event atomically:

1. completes the product;
2. stores its server-local completion time;
3. updates the completed quantity;
4. closes the active tray assignment;
5. completes the order when all its products are done.

The dashboard's order-completion endpoint refuses completion while any physical
product lacks a `done` event.

## Data sources

`data_sources` contains the physical `real` source with ID `1`. Measurements that
omit `data_source_id` use this source. The schema also supports future simulation
sources. If physical and simulated systems later use different sensor identifiers,
an interpretation/mapping table can be added without changing the dashboard
contract.

## API endpoints

The dashboard endpoints are:

- `POST /auth/login` (temporary pass-through until authentication is implemented)
- `GET /user-types`
- `GET /process/graph`
- `GET /process/{process_id}`
- `GET /sensors`
- `POST /sensors/chart`
- `WS /ws/sensors/chart`
- `GET /products`
- `GET /orders`
- `POST /orders/add`
- `GET /orders/completed`
- `GET /orders/current`
- `GET /orders/{order_id}`
- `DELETE /orders/{order_id}/delete`
- `POST /orders/{order_id}/complete`
- `GET /kpis/{user_id}`
- `POST /trays/{nfc_tag_id}/assign-next`
- `POST /products/{product_instance_id}/events`

The generated HTTP API documentation is available at `/docs`.
