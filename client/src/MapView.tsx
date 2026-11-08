import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
maplibregl.setWorkerUrl(workerUrl);
import { MapboxOverlay } from "@deck.gl/mapbox";
import { PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { Coord, Draft, Scenario, Solution, Validation } from "./types";
import { position } from "./replay";
import { it } from "./it";
import "maplibre-gl/dist/maplibre-gl.css";
import { colors } from "./colors";
interface Props {
  center: Coord;
  draft: Draft | null;
  scenario: Scenario | null;
  solution: Solution | null;
  validation: Validation | null;
  elapsed: number | null;
  selected: string | null;
  tool: string;
  onSelect: (id: string) => void;
  onPoint: (point: Coord) => void;
  onFrame: (ms: number) => void;
}
export default function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null),
    map = useRef<maplibregl.Map | null>(null),
    overlay = useRef<MapboxOverlay | null>(null);
  const callbacks = useRef(props);
  callbacks.current = props;
  const [offline, setOffline] = useState(false),
    [unsupported, setUnsupported] = useState(false);
  useEffect(() => {
    if (!container.current) return;
    let current: maplibregl.Map;
    try {
      current = new maplibregl.Map({
        container: container.current,
        center: props.center,
        zoom: 13,
        style: {
          version: 8,
          sources: {},
          layers: [
            {
              id: "base",
              type: "background",
              paint: { "background-color": "#141e29" },
            },
          ],
        },
        attributionControl: false,
      });
    } catch {
      setUnsupported(true);
      return;
    }
    map.current = current;
    const deck = new MapboxOverlay({ interleaved: false, layers: [] });
    overlay.current = deck;
    current.addControl(deck);
    current.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      "top-right",
    );
    current.addControl(
      new maplibregl.AttributionControl({
        compact: false,
        customAttribution:
          '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a> contributors · OpenFreeMap · OpenMapTiles',
      }),
      "bottom-right",
    );
    current.on("click", (event) => {
      if (callbacks.current.tool !== "select")
        callbacks.current.onPoint([event.lngLat.lng, event.lngLat.lat]);
    });
    current.on("error", () => setOffline(true));
    const abort = new AbortController();
    fetch("https://tiles.openfreemap.org/styles/dark", { signal: abort.signal })
      .then((r) => {
        if (!r.ok) throw Error();
        return r.json();
      })
      .then((style) => {
        current.setStyle(style);
        setOffline(false);
      })
      .catch(() => {
        if (!abort.signal.aborted) setOffline(true);
      });
    let last = performance.now(),
      frame = 0,
      samples: number[] = [];
    const measure = (now: number) => {
      samples.push(now - last);
      last = now;
      if (samples.length === 60) {
        callbacks.current.onFrame([...samples].sort((a, b) => a - b)[56]);
        samples = [];
      }
      frame = requestAnimationFrame(measure);
    };
    frame = requestAnimationFrame(measure);
    return () => {
      cancelAnimationFrame(frame);
      abort.abort();
      current.remove();
      map.current = null;
      overlay.current = null;
    };
  }, []);
  useEffect(() => {
    map.current?.jumpTo({ center: props.center });
  }, [props.center[0], props.center[1]]);
  const fixedLayers = useMemo(() => {
    const points =
      props.scenario?.locations.map((p) => ({
        id: p.id,
        coordinate: p.snapped,
      })) ??
      (props.draft
        ? [
            { id: "depot", coordinate: props.draft.depot },
            ...props.draft.stops.map((s) => ({
              id: s.id,
              coordinate: s.coordinate!,
            })),
          ]
        : []);
    const paths = (props.solution?.routes ?? []).flatMap((route, index) => {
      const ids = ["depot", ...route.stop_ids, "depot"];
      if (!route.stop_ids.length) return [];
      return ids.slice(1).flatMap((target, i) => {
        const leg = props.scenario?.legs.find(
          (l) => l.source === ids[i] && l.target === target,
        );
        if (!leg?.reachable) return [];
        const pid = route.path_ids?.[i];
        const alt = pid ? leg.alternatives?.find((a) => a.id === pid) : undefined;
        return [{ path: alt?.geometry ?? leg.geometry, color: colors[index % 5] }];
      });
    });
    return [
      new PathLayer({
        id: "roads",
        data: paths,
        getPath: (d) => d.path,
        getColor: (d) => d.color,
        getWidth: 4,
        widthUnits: "pixels",
        opacity: 0.85,
      }),
      new ScatterplotLayer({
        id: "points",
        data: points,
        getPosition: (d) => d.coordinate,
        getRadius: (d) => (d.id === "depot" ? 9 : 6),
        radiusUnits: "pixels",
        getFillColor: (d) =>
          d.id === props.selected
            ? [255, 255, 255]
            : d.id === "depot"
              ? [60, 219, 183]
              : [145, 162, 182],
        stroked: true,
        getLineColor: [16, 23, 34],
        lineWidthMinPixels: 2,
        pickable: props.tool === "select",
        onClick: (info) => {
          if (info.object) callbacks.current.onSelect(info.object.id);
        },
        updateTriggers: { getFillColor: [props.selected] },
      }),
      new TextLayer({
        id: "point-labels",
        data: points,
        getPosition: (d) => d.coordinate,
        getText: (d) => (d.id === "depot" ? "D" : d.id.replace("stop-", "")),
        getSize: 11,
        getColor: [222, 231, 242],
        getPixelOffset: [0, 17],
        fontFamily: "monospace",
        fontWeight: 600,
      }),
    ];
  }, [props.draft, props.scenario, props.solution, props.selected, props.tool]);
  useEffect(() => {
    const vehicles =
      props.elapsed !== null && props.scenario && props.validation
        ? props.validation.vehicles
            .filter((v) => v.stop_ids.length)
            .map((v, i) => ({
              id: v.vehicle_id,
              ...position(props.scenario!, v, props.elapsed!),
              color: colors[i % 5],
            }))
        : [];
    overlay.current?.setProps({
      layers: [
        ...fixedLayers,
        new ScatterplotLayer({
          id: "vehicles",
          data: vehicles,
          getPosition: (d) => d.coordinate,
          getRadius: 9,
          radiusUnits: "pixels",
          getFillColor: (d) => d.color,
          stroked: true,
          getLineColor: [255, 255, 255],
          lineWidthMinPixels: 2,
        }),
        new TextLayer({
          id: "vehicle-labels",
          data: vehicles,
          getPosition: (d) => d.coordinate,
          getText: (d) => d.id,
          getSize: 11,
          getColor: [255, 255, 255],
          getPixelOffset: [0, -17],
          fontFamily: "monospace",
        }),
      ],
    });
  }, [fixedLayers, props.scenario, props.validation, props.elapsed]);
  return (
    <div className={`map-shell ${props.tool !== "select" ? "crosshair" : ""}`}>
      <div ref={container} className="map" data-testid="route-map" />
      <div className="map-grid" aria-hidden="true" />
      <div className="map-label">
        <span className="live-dot" />
        {it.network}
        <small>OSM / OSRM · MLD</small>
      </div>
      {(offline || unsupported) && (
        <div className="map-warning" role="status">
          {it.offlineMap}
        </div>
      )}
      {props.tool !== "select" && (
        <div className="map-hint">
          {props.tool === "add" ? it.clickAdd : it.clickMove}
          {props.tool === "add" && props.draft && (
            <button onClick={() => props.onPoint(props.draft!.depot)}>
              {it.addAtDepot}
            </button>
          )}
        </div>
      )}
      <div className="map-scale">
        LOCAL ROUTING ENGINE <span>CPU</span>
      </div>
    </div>
  );
}
