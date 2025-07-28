from fastapi import APIRouter

# from app.dataconnect_manager import get_all_traffic_data, get_traffic_data_by_location, add_traffic_data


router = APIRouter()

# @router.post("/traffic-data")
# async def ingest_traffic_data(data: TrafficData, current_user: dict = Depends(get_current_active_user)):
#     """Endpoint to ingest real-time traffic data. Requires authentication."""
#     try:
#         result = await add_traffic_data(data.congestion, data.location)
#         return {"message": "Traffic data ingested successfully", "data": result}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Failed to save traffic data: {str(e)}")

# @router.get("/traffic-data")
# async def get_traffic_data(
#     limit: int = Query(100, ge=1, le=1000),
#     current_user: dict = Depends(get_current_active_user)
# ): # Added limit parameter
#     """Endpoint to retrieve traffic data for visualization. Requires authentication."""
#     try:
#         result = await get_all_traffic_data()
#         return result
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Failed to retrieve traffic data: {str(e)}")

# @router.get("/traffic-data/{location}")
# async def get_traffic_data_by_location_route(
#     location: str,
#     current_user: dict = Depends(get_current_active_user)
# ):
#     """Endpoint to retrieve traffic data for visualization. Requires authentication."""
#     try:
#         result = await get_traffic_data_by_location(location)
#         return result
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Failed to retrieve traffic data: {str(e)}")
