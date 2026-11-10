import { useEffect, useRef, useState } from "react";

const STYLE = "https://tiles.openfreemap.org/styles/positron";

export function PointsMap({ points }: { points: [number, number][] }) {
  const box = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (points.length === 0 || !box.current) return;
    let map: import("maplibre-gl").Map | undefined;
    let gone = false;
    (async () => {
      try {
        const [{ default: maplibre }] = await Promise.all([
          import("maplibre-gl"),
          import("maplibre-gl/dist/maplibre-gl.css"),
        ]);
        if (gone || !box.current) return;
        const lngLat = points.map(([lat, lon]) => [lon, lat] as [number, number]);
        map = new maplibre.Map({
          container: box.current,
          style: STYLE,
          center: lngLat[0] ?? [-73.97, 40.73],
          zoom: 11,
          attributionControl: { compact: true },
        });
        map.addControl(new maplibre.NavigationControl({ showCompass: false }));
        map.on("load", () => {
          if (!map) return;
          map.addSource("pts", {
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
          map.addLayer({
            id: "pts",
            type: "circle",
            source: "pts",
            paint: {
              "circle-radius": 5,
              "circle-color": "#c2410c",
              "circle-opacity": 0.75,
              "circle-stroke-color": "#ffffff",
              "circle-stroke-width": 1,
            },
          });
          const b = new maplibre.LngLatBounds(lngLat[0], lngLat[0]);
          lngLat.forEach((c) => b.extend(c));
          map.fitBounds(b, { padding: 40, maxZoom: 15, duration: 0 });
        });
        map.on("error", () => undefined);
      } catch {
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
