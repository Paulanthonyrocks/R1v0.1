from fastapi import APIRouter

from .routers import (
    alerts,
    auth,
    events,
    feeds,
    incidents,
    logs,
    pavement,
    personalized_routes,
    route_history,
    routes,
    token,
    video,
    weather,
)

api = APIRouter()

api.include_router(auth.router, prefix="/auth", tags=["auth"])
api.include_router(token.router, prefix="/token", tags=["token"])
api.include_router(feeds.router, prefix="/feeds", tags=["feeds"])
api.include_router(routes.router, prefix="/routes", tags=["routes"])
api.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
api.include_router(events.router, prefix="/events", tags=["events"])
api.include_router(pavement.router, prefix="/pavement", tags=["pavement"])
api.include_router(weather.router, prefix="/weather", tags=["weather"])
api.include_router(
    personalized_routes.router,
    prefix="/personalized_routes",
    tags=["personalized_routes"],
)
api.include_router(
    route_history.router, prefix="/route_history", tags=["route_history"]
)
api.include_router(incidents.router, prefix="/incidents", tags=["incidents"])
api.include_router(logs.router, prefix="/logs", tags=["logs"])
api.include_router(video.router, prefix="/video", tags=["video"])
