-- Infrastructure bootstrap only.
--
-- Application reference data is maintained in db/init/data/db_init_data.xlsx
-- and imported by `python -m app.init_db`. Keep the physical source on ID 1
-- because direct database writers may rely on the measurements.data_source_id
-- default before they are updated to send the source explicitly.

INSERT INTO public.data_sources (
    data_source_id,
    source_key,
    source_name,
    source_type,
    enabled
)
VALUES (1, 'real', 'Physical system', 'physical', TRUE)
ON CONFLICT (data_source_id) DO UPDATE
SET
    source_key = EXCLUDED.source_key,
    source_name = EXCLUDED.source_name,
    source_type = EXCLUDED.source_type,
    enabled = EXCLUDED.enabled;

SELECT setval(
    pg_get_serial_sequence('public.data_sources', 'data_source_id'),
    GREATEST((SELECT MAX(data_source_id) FROM public.data_sources), 1)
);
