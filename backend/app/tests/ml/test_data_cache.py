import unittest
from datetime import datetime, timedelta

from app.ml.data_cache import TrafficDataCache

class TestTrafficDataCache(unittest.TestCase):

    def setUp(self):
        self.cache = TrafficDataCache(max_history_hours=1) # Short history for easier testing

    def test_get_all_location_summaries_empty_cache(self):
        summaries = self.cache.get_all_location_summaries()
        self.assertEqual(summaries, [])

    def test_get_all_location_summaries_single_location_single_point(self):
        lat1, lon1 = 34.05, -118.25
        ts1 = datetime.now() - timedelta(minutes=30)
        data1 = {'vehicle_count': 10, 'average_speed': 50.5, 'congestion_score': 20.0}

        self.cache.add_data_point(lat1, lon1, ts1, data1)

        summaries = self.cache.get_all_location_summaries()

        self.assertEqual(len(summaries), 1)
        summary1 = summaries[0]

        self.assertEqual(summary1['id'], f"{round(lat1, 4)},{round(lon1, 4)}")
        self.assertEqual(summary1['name'], f"Node at ({round(lat1, 4):.4f}, {round(lon1, 4):.4f})")
        self.assertEqual(summary1['latitude'], round(lat1, 4))
        self.assertEqual(summary1['longitude'], round(lon1, 4))
        self.assertEqual(summary1['timestamp'], ts1)
        self.assertEqual(summary1['vehicle_count'], data1['vehicle_count'])
        self.assertEqual(summary1['average_speed'], data1['average_speed'])
        self.assertEqual(summary1['congestion_score'], data1['congestion_score'])

    def test_get_all_location_summaries_multiple_locations_multiple_points(self):
        lat1, lon1 = 34.05, -118.25
        ts1_old = datetime.now() - timedelta(minutes=45)
        data1_old = {'vehicle_count': 5, 'average_speed': 60.0, 'congestion_score': 10.0, 'custom_field': 'A'}
        ts1_new = datetime.now() - timedelta(minutes=15)
        data1_new = {'vehicle_count': 15, 'average_speed': 40.0, 'congestion_score': 30.0, 'custom_field': 'B'}

        self.cache.add_data_point(lat1, lon1, ts1_old, data1_old)
        self.cache.add_data_point(lat1, lon1, ts1_new, data1_new) # This is the latest for loc1

        lat2, lon2 = 40.71, -74.00
        ts2 = datetime.now() - timedelta(minutes=10)
        data2 = {'vehicle_count': 25, 'average_speed': 30.0, 'congestion_score': 65.0, 'weather': 'cloudy'}
        self.cache.add_data_point(lat2, lon2, ts2, data2)

        summaries = self.cache.get_all_location_summaries()
        self.assertEqual(len(summaries), 2)

        # Sort by ID to ensure consistent order for assertions
        summaries.sort(key=lambda s: s['id'])

        summary_loc1 = next(s for s in summaries if s['id'] == f"{round(lat1, 4)},{round(lon1, 4)}")
        summary_loc2 = next(s for s in summaries if s['id'] == f"{round(lat2, 4)},{round(lon2, 4)}")

        self.assertEqual(summary_loc1['timestamp'], ts1_new)
        self.assertEqual(summary_loc1['vehicle_count'], data1_new['vehicle_count'])
        self.assertEqual(summary_loc1['average_speed'], data1_new['average_speed'])
        self.assertEqual(summary_loc1['congestion_score'], data1_new['congestion_score'])
        self.assertEqual(summary_loc1['custom_field'], data1_new['custom_field']) # Check extra fields

        self.assertEqual(summary_loc2['timestamp'], ts2)
        self.assertEqual(summary_loc2['vehicle_count'], data2['vehicle_count'])
        self.assertEqual(summary_loc2['average_speed'], data2['average_speed'])
        self.assertEqual(summary_loc2['congestion_score'], data2['congestion_score'])
        self.assertEqual(summary_loc2['weather'], data2['weather'])


    def test_get_all_location_summaries_data_cleaned_if_too_old(self):
        lat1, lon1 = 34.05, -118.25
        # Data older than max_history_hours (1 hour for this test setup)
        ts_too_old = datetime.now() - timedelta(hours=2)
        data_old = {'vehicle_count': 5, 'average_speed': 60.0, 'congestion_score': 10.0}

        self.cache.add_data_point(lat1, lon1, ts_too_old, data_old)

        # _clean_old_data is called internally by add_data_point, but let's ensure it works
        # To explicitly trigger it for this location if it wasn't empty before:
        # self.cache._clean_old_data(self.cache._get_location_key(lat1, lon1))
        # However, get_all_location_summaries should simply not find it if it was cleaned.
        # If add_data_point correctly cleans, then the list for this key might be empty.

        # If the cache was empty, add_data_point adds then cleans. If data was already there, it appends then cleans.
        # If the only point added is too old, it will be removed.

        summaries = self.cache.get_all_location_summaries()
        # Depending on strictness, either the summary is not there, or it might be there if
        # _clean_old_data is only called when new data is added.
        # The current implementation of add_data_point always calls _clean_old_data.
        # So, if ts_too_old is the *only* point, it gets added, then immediately removed.
        self.assertEqual(len(summaries), 0, "Expected no summaries as the only data point was too old and should be cleaned.")


    def test_get_location_key_rounding(self):
        # Test if locations that are very close map to the same key due to rounding
        lat1, lon1 = 34.12345, -118.12345
        lat2, lon2 = 34.12340, -118.12340 # Should round to the same as lat1, lon1
        lat3, lon3 = 34.12355, -118.12355 # Should round to different

        key1 = self.cache._get_location_key(lat1, lon1)
        key2 = self.cache._get_location_key(lat2, lon2)
        key3 = self.cache._get_location_key(lat3, lon3)

        self.assertEqual(key1, key2)
        self.assertNotEqual(key1, key3)

if __name__ == '__main__':
    unittest.main()
