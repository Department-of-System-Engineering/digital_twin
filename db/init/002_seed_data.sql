-- Initial reference data required by the application.
--
-- Keep every statement idempotent because this file is used in two ways:
--   1. PostgreSQL executes it when a new database volume is initialized.
--   2. app.init_db executes it on every deployment for existing databases.
--
-- Separate statements with the marker below so app.init_db can execute them
-- individually. The marker must be on its own line.

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

-- seed-statement

SELECT setval(
    pg_get_serial_sequence('public.data_sources', 'data_source_id'),
    GREATEST((SELECT MAX(data_source_id) FROM public.data_sources), 1)
);

-- seed-statement

INSERT INTO public.process_configurations (configuration_name, is_active)
SELECT 'Default', TRUE
WHERE NOT EXISTS (
    SELECT 1 FROM public.process_configurations WHERE is_active
);

-- seed-statement

INSERT INTO public.product_types (product_type_name, max_quantity)
VALUES
    ('A', 20),
    ('B', 30),
    ('C', 10),
    ('D', 50),
    ('Special', 5)
ON CONFLICT (product_type_name) DO NOTHING;

-- seed-statement

INSERT INTO public.user_types (user_type_id, user_type_name)
VALUES
    (1, 'Customer'),
    (2, 'Operator'),
    (3, 'Technician'),
    (4, 'Shift Supervisor'),
    (5, 'Engineer'),
    (6, 'Manager')
ON CONFLICT (user_type_id) DO UPDATE
SET user_type_name = EXCLUDED.user_type_name;

-- seed-statement

SELECT setval(
    pg_get_serial_sequence('public.user_types', 'user_type_id'),
    GREATEST((SELECT MAX(user_type_id) FROM public.user_types), 1)
);
