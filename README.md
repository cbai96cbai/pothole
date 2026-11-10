pothole: a daily pipeline over public NYC 311 data, with a repair agent and an analyst.

The site: `python -m pothole.sitedata .` writes its data files to `web/src/data/` (not committed) and
the schemas to `web/schema/`; then `npm ci && npm run build` in `web/`.
