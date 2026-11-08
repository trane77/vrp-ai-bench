# Interface and observation

## Visual direction

Dark graphite/navy surfaces, restrained luminous accents, clear typography and limited translucency. Distinct van colors also use labels/icons so color is never the only signal. High-contrast analysis mode and optional cinematic presentation mode share the same underlying results.

MapLibre renders the city; deck.gl renders route and event overlays. Prefer readable 2D by default; optional inclined view and buildings only when dataset coverage allows. Respect map-data attribution. No fabricated buildings, road connectivity or solver activity.

## Layout

- Center: city, depot, stops, route geometry and selected vehicle.
- Left: scenario, fleet, service mode, windows, duration cap, objective configuration and M7 operational constraints (alternative OSRM paths, mandatory driver break).
- Right: selected stop/vehicle details, packages, pickup/delivery loads, arrival/service/break/departure times, deadline slack and estimated fuel.
- Bottom: vehicle timelines (displaying driving, waiting, service and mandatory driver break segments per van) and replay controls.
- Comparison view: synchronized maps, matching budget position and paired metrics.
- R&D laboratory view: Monte Carlo stochastic robustness stress-testing, single-path vs multi-path OSRM trade-off table, multi-scale campaign launcher and learned model inspector.

## Two distinct observation modes

Search replay shows recorded candidate changes, accepted solutions and improvements. Route playback shows the execution of a selected finished solution. Clearly label each mode and display compute elapsed time separately from simulated city time.

Search may improve an infeasible candidate without producing a usable tour. Show candidate violations separately from best feasible solution; never replace a valid best-tour display with an unlabeled invalid candidate.

Replay supports pause, speed, step to the next recorded event, seek and jump to the next feasible-best update. Route playback interpolates vehicles along saved road geometry with waiting/service phases. Do not infer real solver explanations from animation; show recorded move type and measured metric changes.

## Performance and accessibility acceptance

Measure rendering on the actual client independently of solver time. Set an explicit frame-time target after the first city prototype. Batch streaming updates, avoid rerendering the entire application per event, and provide reduced-motion mode. Timeline and metrics remain readable with animations disabled.

Verify loading, empty data, unreachable stops, no feasible solution found, invalid inputs, solver failure, cancellation, connection loss and reconnection. The interface must expose uncertainty and status without raw stack traces or misleading success messages.
