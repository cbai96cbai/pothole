import { useEffect, useRef, useState } from "react";

const STYLE = "https://tiles.openfreemap.org/styles/positron";

declare global {
  interface Window {
    __potholeMap?: import("maplibre-gl").Map;
  }
}

export function PointsMap({ points }: { points: [number, number][] }) {
  const box = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (points.length === 0 || !box.current) return;
    let map: import("maplibre-gl").Map | undefined;
    let gone = false;
    (async () => {
      try {
        const [maplibre] = await Promise.all([
          import("maplibre-gl"),
          import("maplibre-gl/dist/maplibre-gl.css"),
        ]);
        if (gone || !box.current) return;
        if (import.meta.env.PROD) {
          maplibre.setWorkerUrl(`${import.meta.env.BASE_URL}maplibre/maplibre-gl-worker.mjs`);
        }
        const lngLat = points.map(([lat, lon]) => [lon, lat] as [number, number]);
        const made = new maplibre.Map({
          container: box.current,
          style: STYLE,
          center: lngLat[0] ?? [-73.97, 40.73],
          zoom: 11,
          attributionControl: { compact: true },
        });
        map = made;
        if (import.meta.env.VITE_TEST_HOOKS === "1") window.__potholeMap = made;
        made.addControl(new maplibre.NavigationControl({ showCompass: false }));
        made.on("error", (e) => console.error(`map error (style ${STYLE}):`, e.error));
        // the points are added after the style has loaded; the view is fitted to them
        made.on("load", () => {
          made.addSource("pts", {
            type: "geojson",
            data: {
              type: "FeatureCollection",
              features: lngLat.map((c) => ({
                type: "Feature",
                properties: {},
                geometry: { type: "Point", coordinates: c },
              })),
            },
          });
          made.addLayer({
            id: "pts",
            type: "circle",
            source: "pts",
            paint: {
              "circle-radius": 6,
              "circle-color": "#c2410c",
              "circle-opacity": 0.8,
              "circle-stroke-color": "#ffffff",
              "circle-stroke-width": 1.5,
            },
          });
          made.resize();
          const bounds = new maplibre.LngLatBounds(lngLat[0], lngLat[0]);
          lngLat.forEach((c) => bounds.extend(c));
          made.fitBounds(bounds, {
            padding: { top: 50, bottom: 80, left: 50, right: 60 },
            maxZoom: 15,
            duration: 0,
          });
        });
      } catch (e) {
        console.error("the map could not start:", e);
        if (!gone) setFailed(true);
      }
    })();
    return () => {
      gone = true;
      map?.remove();
    };
  }, [points]);

  if (points.length === 0) return <p className="muted">No complaint locations recorded.</p>;
  return (
    <figure className="mapbox">
      {failed ? (
        <p className="muted">The map could not start in this browser. {points.length} points were drawn.</p>
      ) : (
        <div ref={box} className="map" role="img" aria-label={`Map of ${points.length} complaint locations`} />
      )}
      <figcaption className="muted">
        Complaint locations on the flagged day ({points.length} points, as filed; no addresses).
        Map: OpenFreeMap, data © OpenStreetMap contributors.
      </figcaption>
    </figure>
  );
}
