# Vulture whitelist — names that ARE used but look dead to static analysis because they're mandated by
# an external interface signature (the caller passes them positionally). Regenerate/extend after review:
#   uvx vulture src --min-confidence 80 --make-whitelist >> .vulture_whitelist.py
# Advisory only (poe deadcode); not part of `all`. Keep this list tight — a genuine dead name hidden
# here defeats the point.

# structlog processor protocol: (logger, method_name, event_dict) — first two are required positionally.
logger
method_name

# OpenTelemetry SpanExporter override signatures require these params even when unused.
timeout_millis

# POSIX signal handler protocol: handler(signum, frame).
signum
