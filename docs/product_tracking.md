# Product tracking integration

Node-RED sends station events to the backend instead of changing product
statuses directly. Every request must include the configured `X-API-Key`.

## Assigning a tray at Preparation

When an empty NFC tag arrives, Node-RED calls:

```http
POST /trays/{nfc_tag_id}/assign-next
Content-Type: application/json

{}
```

The backend atomically selects the next queued product from active orders. It
orders work by priority, order date, order-item position and product sequence.
The response contains `productInstanceId` and `orderItemId`; Node-RED writes
these values to the NFC tag. An optional `orderId` in the request restricts the
selection to one order.

## Reporting station events

```http
POST /production/events
X-API-Key: <INBOUND_API_KEY>
Content-Type: application/json

{
  "eventId": "prep-arrived-20260924-123456-ABC123",
  "productInstanceId": 12,
  "orderItemId": 4,
  "stationKey": "preparation",
  "nextStationKey": null,
  "state": "arrived",
  "time": "2026-09-24T12:34:56"
}
```

Supported station keys are the stable `asset_key` values from the seed
workbook, currently `preparation`, `assembly1`, `assembly2` and `visual_qc`.
Use `arrived` when a tag is read at a station and `departed` when the station
reports that the product left. A departed product is displayed at the next
routing step, which is the corresponding conveyor node.

The Preparation asset owns two internal process steps that cannot be separated
by hardware. The graph therefore collapses them into
`Base preparation - Preparation Station`. Arrival maps to the first internal
step and departure to the last one.

The process graph also contains two QC rework branches:

- `visual_qc -> conveyor (step_04) -> assembly1`
- `visual_qc -> conveyor (step_06) -> assembly2`

For a QC `departed` event, `nextStationKey` is required and must be either
`assembly1` or `assembly2`. The backend stores the selected first conveyor
step with the event, so the dashboard displays the product on only the chosen
return path. Events on a process step with only one outgoing route do not need
`nextStationKey`.

The future QC completion signal uses `state: "done"`. The backend records its
location as the warehouse step, completes the product and releases the tray.
Completed products remain visible until every product in their order is done;
then the complete order disappears from the live process graph.

`eventId` must be stable across retries. Duplicate requests return the existing
event instead of inserting a second tracking row.

## Dashboard transport

- `GET /process/products` returns the current complete occupancy snapshot.
- `WS /ws/process/products` sends a new complete snapshot whenever it changes.
- Each graph node displays the product count and its hover panel lists
  `product_instance_id : product_type_name`.
