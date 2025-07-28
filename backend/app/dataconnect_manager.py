# from firebase_admin import dataconnect

# cred = credentials.Certificate("path/to/serviceAccountKey.json")
# firebase_admin.initialize_app(cred)


# def get_all_traffic_data():
#     return dc.execute("query ListTrafficData")

# def get_traffic_data_by_location(location: str):
#     return dc.execute("query GetTrafficDataByLocation", {"location": location})

# def add_traffic_data(congestion: float, location: str):
#     return dc.execute("mutation AddTrafficData", {"congestion": congestion, "location": location})
