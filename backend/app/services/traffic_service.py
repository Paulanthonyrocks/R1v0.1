from app.models.traffic_data import TrafficData
from app.database import get_db


async def get_all_traffic_data():
    db = get_db()
    return await db.query(TrafficData).all()


async def get_traffic_data_by_location(location: str):
    db = get_db()
    return await db.query(TrafficData).filter(TrafficData.location == location).all()


async def add_traffic_data(congestion: float, location: str):
    db = get_db()
    traffic_data = TrafficData(congestion=congestion, location=location)
    db.add(traffic_data)
    await db.commit()
    return traffic_data
