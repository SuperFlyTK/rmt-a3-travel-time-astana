# Historical route observations

Checked on 24 October 2026 after the team confirmed submission by 25 October at 18:00 Astana time.

The delivered sample currently contains observations from 24 October. The prospective follow-up covers 24 and 25 October. A large number of route requests does not increase the number of observed dates. The earlier two-week proposal was not executed and must not be described as observed data.

Google Routes ComputeRoutes permits a past departureTime only for TRANSIT. Our driving requests cannot recover the responses that would have been obtained on earlier dates. Traffic predictions using historical averages are not an archive of those missing responses. [Google ComputeRoutes reference](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes).

The hosted openrouteservice API has no live traffic, and its maintainers say the statistical traffic feature is unavailable on api.openrouteservice.org. Changing a date cannot recover earlier live congestion from this control. [ORS maintainer explanation](https://ask.openrouteservice.org/t/isochrone-trafic-data/7875).

TomTom offers a separate Traffic Stats archive, including a free evaluation, for historical route and area statistics. Kazakhstan is not listed in its published coverage table. This does not establish impossibility under a separately negotiated service, but it does not confirm usable Astana coverage or access with the existing routing key. No historical TomTom dataset has been obtained. [Product introduction](https://docs.tomtom.com/traffic-stats/documentation/user-guide/introduction), [coverage](https://docs.tomtom.com/traffic-stats/documentation/product-information/market-coverage), [evaluation information](https://docs.tomtom.com/traffic-stats/documentation/product-information/faq).

Google Roads Management Insights is also a separate product: its historical travel-time table begins accumulating after a route is registered. Its documentation does not establish retrospective backfill of our unregistered routes. [Accumulated road data](https://developers.google.com/maps/tt/roads-selection/historical-roads-data).

Therefore the study keeps the genuine short prospective sample and explicitly reports its date coverage. An independently sourced archive, if later obtained, would be a separate secondary dataset with its own provenance; it would not be relabelled as earlier Google-ORS paired requests.
